import datetime
from asyncio import Event
from contextlib import asynccontextmanager
from typing import Optional

from aioquic.quic.configuration import QuicConfiguration
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from rsocket.helpers import single_transport_provider
from rsocket.rsocket_base import RSocketBase
from rsocket.rsocket_client import RSocketClient
from rsocket.transports.aioquic_transport import rsocket_connect, rsocket_serve
from tests.rsocket.helpers import assert_no_open_streams


def generate_certificate(*, alternative_names, common_name, hash_algorithm, key):
    subject = issuer = x509.Name(
        [x509.NameAttribute(x509.NameOID.COMMON_NAME, common_name)]
    )

    builder = (
        x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow())
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=10))
    )
    if alternative_names:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(name) for name in alternative_names]
            ),
            critical=False,
        )
    cert = builder.sign(key, hash_algorithm)
    return cert, key


def generate_ec_certificate(common_name, alternative_names=None, curve=ec.SECP256R1):
    if alternative_names is None:
        alternative_names = []

    key = ec.generate_private_key(curve=curve)
    return generate_certificate(
        alternative_names=alternative_names,
        common_name=common_name,
        hash_algorithm=hashes.SHA256(),
        key=key,
    )


@asynccontextmanager
async def pipe_factory_quic(unused_tcp_port,
                            client_arguments=None,
                            server_arguments=None):
    certificate, private_key = generate_ec_certificate(common_name="localhost")

    server_configuration = QuicConfiguration(
        certificate=certificate,
        private_key=private_key,
        is_client=False
    )

    client_configuration = QuicConfiguration(
        is_client=True
    )
    cadata = certificate.public_bytes(serialization.Encoding.PEM)
    client_configuration.load_verify_locations(cadata=cadata, cafile=None)

    server: Optional[RSocketBase] = None
    wait_for_server = Event()

    def store_server(new_server):
        nonlocal server
        server = new_server
        wait_for_server.set()

    quic_server = await rsocket_serve(host='localhost',
                                      port=unused_tcp_port,
                                      configuration=server_configuration,
                                      on_server_create=store_server,
                                      **(server_arguments or {}))

    # test_overrides = {'keep_alive_period': timedelta(minutes=20)}
    client_arguments = client_arguments or {}
    # client_arguments.update(test_overrides)
    transport = await rsocket_connect('localhost', unused_tcp_port,
                                      configuration=client_configuration)

    async with RSocketClient(single_transport_provider(transport),
                             **client_arguments) as client:
        await wait_for_server.wait()
        yield server, client
        await server.close()
        assert_no_open_streams(client, server)

    quic_server.cancel()
    await quic_server

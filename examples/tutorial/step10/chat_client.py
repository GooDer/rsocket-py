import asyncio
import json
import logging

from reactivex import operators

from examples.tutorial.step10.models import Message
from rsocket.extensions.helpers import composite, route, metadata_item
from rsocket.extensions.mimetypes import WellKnownMimeTypes
from rsocket.frame_helpers import ensure_bytes
from rsocket.helpers import single_transport_provider
from rsocket.payload import Payload
from rsocket.reactivex.reactivex_client import ReactiveXClient
from rsocket.rsocket_client import RSocketClient
from rsocket.transports.tcp import TransportTCP

chat_session_mimetype = b'chat/session-id'


def print_private_message(data):
    message = Message(**json.loads(data))
    print(f'{message.user}: {message.content}')


async def listen_for_messages(client, session_id):
    await ReactiveXClient(client).request_stream(Payload(metadata=composite(
        route('messages.incoming'),
        metadata_session_id(session_id)
    ))).pipe(
        # operators.take(1),
        operators.do_action(on_next=lambda value: print_private_message(value.data),
                            on_error=lambda exception: print(exception)))


def encode_dataclass(obj):
    return ensure_bytes(json.dumps(obj.__dict__))


async def login(client, username: str):
    payload = Payload(ensure_bytes(username), composite(route('login')))
    return (await client.request_response(payload)).data


def new_private_message(session_id: bytes, to_user: str, message: str):
    print(f'Sending {message} to user {to_user}')

    return Payload(encode_dataclass(Message(to_user, message)),
                   composite(route('message'), metadata_session_id(session_id)))


def metadata_session_id(session_id):
    return metadata_item(session_id, chat_session_mimetype)


async def main():
    connection1 = await asyncio.open_connection('localhost', 6565)

    async with RSocketClient(single_transport_provider(TransportTCP(*connection1)),
                             metadata_encoding=WellKnownMimeTypes.MESSAGE_RSOCKET_COMPOSITE_METADATA) as client1:
        connection2 = await asyncio.open_connection('localhost', 6565)

        async with RSocketClient(single_transport_provider(TransportTCP(*connection2)),
                                 metadata_encoding=WellKnownMimeTypes.MESSAGE_RSOCKET_COMPOSITE_METADATA) as client2:
            # User 1 logs in and listens for messages
            session1 = await login(client1, 'user1')

            task = asyncio.create_task(listen_for_messages(client1, session1))

            # User 2 logs in and send a message to user 1
            session2 = await login(client2, 'user2')
            await client2.request_response(new_private_message(session2, 'user1', 'some message'))

            messages_done = asyncio.Event()
            task.add_done_callback(lambda: messages_done.set())
            await messages_done.wait()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

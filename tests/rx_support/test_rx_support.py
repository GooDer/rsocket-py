import asyncio
from asyncio import Future
from typing import Tuple, AsyncGenerator, Optional

import pytest
import rx
from rx import operators
from rx.core import Observer

from reactivestreams.publisher import Publisher
from reactivestreams.subscriber import Subscriber, DefaultSubscriber
from reactivestreams.subscription import Subscription, DefaultSubscription
from rsocket.payload import Payload
from rsocket.request_handler import BaseRequestHandler
from rsocket.rsocket_client import RSocketClient
from rsocket.rsocket_server import RSocketServer
from rsocket.rx_support.rx_rsocket import RxRSocket
from rsocket.streams.stream_from_async_generator import StreamFromAsyncGenerator


async def test_rx_support_request_stream_properly_finished(pipe: Tuple[RSocketServer, RSocketClient]):
    server, client = pipe

    async def generator() -> AsyncGenerator[Tuple[Payload, bool], None]:
        for x in range(3):
            yield Payload('Feed Item: {}'.format(x).encode('utf-8')), x == 2

    class Handler(BaseRequestHandler):
        async def request_stream(self, payload: Payload) -> Publisher:
            return StreamFromAsyncGenerator(generator)

    server.set_handler_using_factory(Handler)

    rx_client = RxRSocket(client)
    received_messages = await rx_client.request_stream(Payload(b'request text'),
                                                       request_limit=2).pipe(
        operators.map(lambda payload: payload.data),
        operators.to_list()
    )

    assert len(received_messages) == 3
    assert received_messages[0] == b'Feed Item: 0'
    assert received_messages[1] == b'Feed Item: 1'
    assert received_messages[2] == b'Feed Item: 2'


@pytest.mark.parametrize('success_count, request_limit', (
        (0, 2),
        (2, 2),
        (3, 2),
))
async def test_rx_support_request_stream_with_error(pipe: Tuple[RSocketServer, RSocketClient],
                                                    success_count,
                                                    request_limit):
    server, client = pipe

    async def generator() -> AsyncGenerator[Tuple[Payload, bool], None]:
        for x in range(success_count):
            yield Payload('Feed Item: {}'.format(x).encode('utf-8')), False

        raise Exception('Some error from responder')

    class Handler(BaseRequestHandler):
        async def request_stream(self, payload: Payload) -> Publisher:
            return StreamFromAsyncGenerator(generator)

    server.set_handler_using_factory(Handler)

    rx_client = RxRSocket(client)

    with pytest.raises(Exception):
        await rx_client.request_stream(
            Payload(b'request text'),
            request_limit=request_limit
        ).pipe(
            operators.map(lambda payload: payload.data),
            operators.to_list()
        )


@pytest.mark.allow_error_log
@pytest.mark.parametrize('success_count, request_limit', (
        (0, 2),
        (2, 2),
        (3, 2),
))
async def test_rx_support_request_channel_with_error_from_requester(
        pipe: Tuple[RSocketServer, RSocketClient],
        success_count,
        request_limit):
    server, client = pipe
    responder_received_error = asyncio.Event()
    server_received_messages = []
    received_error = None

    class ResponderSubscriber(DefaultSubscriber):

        def on_subscribe(self, subscription: Subscription):
            super().on_subscribe(subscription)
            self._subscription = subscription
            self._subscription.request(1)

        def on_next(self, value, is_complete=False):
            if len(value.data) > 0:
                server_received_messages.append(value.data)
            self._subscription.request(1)

        def on_error(self, exception: Exception):
            nonlocal received_error
            received_error = exception
            responder_received_error.set()

    async def generator() -> AsyncGenerator[Tuple[Payload, bool], None]:
        for x in range(success_count):
            yield Payload('Feed Item: {}'.format(x).encode('utf-8')), x == success_count - 1

    class Handler(BaseRequestHandler):
        async def request_channel(self, payload: Payload) -> Tuple[Optional[Publisher], Optional[Subscriber]]:
            return StreamFromAsyncGenerator(generator), ResponderSubscriber()

    server.set_handler_using_factory(Handler)

    rx_client = RxRSocket(client)

    def test_observable(observer: Observer, scheduler):
        observer.on_error(Exception('Some error'))

    await rx_client.request_channel(
        Payload(b'request text'),
        observable=rx.create(test_observable),
        request_limit=request_limit
    ).pipe(
        operators.map(lambda payload: payload.data),
        operators.to_list()
    )

    await responder_received_error.wait()

    assert str(received_error) == 'Some error'


async def test_rx_support_request_stream_immediate_complete(pipe: Tuple[RSocketServer, RSocketClient]):
    server, client = pipe

    class SimpleCompleted(Publisher, DefaultSubscription):

        def request(self, n: int):
            self._subscriber.on_complete()

        def subscribe(self, subscriber: Subscriber):
            self._subscriber = subscriber
            subscriber.on_subscribe(self)

    class Handler(BaseRequestHandler):
        async def request_stream(self, payload: Payload) -> Publisher:
            return SimpleCompleted()

    server.set_handler_using_factory(Handler)

    rx_client = RxRSocket(client)

    result = await rx_client.request_stream(
        Payload(b'request text'),
        request_limit=2
    ).pipe(
        operators.map(lambda payload: payload.data),
        operators.to_list()
    )

    assert len(result) == 0


async def test_rx_support_request_response_properly_finished(pipe: Tuple[RSocketServer, RSocketClient]):
    server, client = pipe

    class Handler(BaseRequestHandler):
        async def request_response(self, payload: Payload) -> Future:
            future = asyncio.Future()
            future.set_result(Payload(b'Response'))
            return future

    server.set_handler_using_factory(Handler)

    rx_client = RxRSocket(client)
    received_message = await rx_client.request_response(Payload(b'request text')).pipe(
        operators.map(lambda payload: payload.data),
        operators.single()
    )

    assert received_message == b'Response'


async def test_rx_support_request_channel_properly_finished(pipe: Tuple[RSocketServer, RSocketClient]):
    server, client = pipe
    server_received_messages = []

    responder_received_all = asyncio.Event()

    async def generator() -> AsyncGenerator[Tuple[Payload, bool], None]:
        for x in range(3):
            yield Payload('Feed Item: {}'.format(x).encode('utf-8')), x == 2

    class ResponderSubscriber(DefaultSubscriber):

        def on_subscribe(self, subscription: Subscription):
            super().on_subscribe(subscription)
            self._subscription = subscription
            self._subscription.request(1)

        def on_next(self, value, is_complete=False):
            if len(value.data) > 0:
                server_received_messages.append(value.data)
            self._subscription.request(1)

        def on_complete(self):
            responder_received_all.set()

    class Handler(BaseRequestHandler):
        async def request_channel(self, payload: Payload) -> Tuple[Optional[Publisher], Optional[Subscriber]]:
            return StreamFromAsyncGenerator(generator), ResponderSubscriber()

    server.set_handler_using_factory(Handler)

    rx_client = RxRSocket(client)

    sent_messages = [b'1', b'2', b'3']
    sent_payloads = [Payload(data) for data in sent_messages]
    received_messages = await rx_client.request_channel(Payload(b'request text'),
                                                        observable=rx.from_list(sent_payloads),
                                                        request_limit=2).pipe(
        operators.map(lambda payload: payload.data),
        operators.to_list()
    )

    await responder_received_all.wait()

    assert server_received_messages == sent_messages

    assert len(received_messages) == 3
    assert received_messages[0] == b'Feed Item: 0'
    assert received_messages[1] == b'Feed Item: 1'
    assert received_messages[2] == b'Feed Item: 2'


async def test_rx_support_request_channel_response_only_properly_finished(pipe: Tuple[RSocketServer, RSocketClient]):
    server, client = pipe

    async def generator() -> AsyncGenerator[Tuple[Payload, bool], None]:
        for x in range(3):
            yield Payload('Feed Item: {}'.format(x).encode('utf-8')), x == 2

    class Handler(BaseRequestHandler):
        async def request_channel(self, payload: Payload) -> Tuple[Optional[Publisher], Optional[Subscriber]]:
            return StreamFromAsyncGenerator(generator), None

    server.set_handler_using_factory(Handler)

    rx_client = RxRSocket(client)

    received_messages = await rx_client.request_channel(Payload(b'request text'),
                                                        request_limit=2).pipe(
        operators.map(lambda payload: payload.data),
        operators.to_list()
    )

    assert len(received_messages) == 3
    assert received_messages[0] == b'Feed Item: 0'
    assert received_messages[1] == b'Feed Item: 1'
    assert received_messages[2] == b'Feed Item: 2'


async def test_rx_rsocket_context_manager(pipe_tcp_without_auto_connect):
    class Handler(BaseRequestHandler):
        async def request_response(self, payload: Payload) -> Future:
            future = asyncio.Future()
            future.set_result(Payload(b'Response'))
            return future

    server, client = pipe_tcp_without_auto_connect
    server.set_handler_using_factory(Handler)

    async with RxRSocket(client) as rx_client:
        received_message = await rx_client.request_response(Payload(b'request text')).pipe(
            operators.map(lambda payload: payload.data),
            operators.single()
        )

        assert received_message == b'Response'

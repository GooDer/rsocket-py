import asyncio
from asyncio import Future
from datetime import timedelta
from typing import Callable, Union, Optional, Coroutine, Tuple

from reactivestreams.publisher import Publisher
from reactivestreams.subscriber import Subscriber
from rsocket.extensions.authentication import Authentication
from rsocket.extensions.authentication_content import AuthenticationContent
from rsocket.extensions.composite_metadata import CompositeMetadata
from rsocket.extensions.mimetypes import WellKnownMimeTypes
from rsocket.extensions.routing import RoutingMetadata
from rsocket.frame import LeaseFrame
from rsocket.handlers.error_stream import ErrorStream
from rsocket.handlers.null_subscrier import NullSubscriber
from rsocket.helpers import always_allow_authenticator, to_milliseconds
from rsocket.logger import logger
from rsocket.payload import Payload
from rsocket.routing.request_router import RequestRouter
from rsocket.rsocket import BaseRequestHandler


class RoutingRequestHandler(BaseRequestHandler):
    __slots__ = (
        'router',
        'data_encoding',
        'metadata_encoding',
        'authentication_verifier',
        '_lease_ttl',
        '_lease_max_requests'
    )

    def __init__(self,
                 socket,
                 router: RequestRouter,
                 authentication_verifier: Optional[
                     Callable[[str, Authentication], Coroutine[None, None, None]]] = always_allow_authenticator,
                 lease_ttl: Optional[timedelta] = None,
                 lease_max_requests: Optional[int] = None):
        super().__init__(socket)
        self.router = router
        self.authentication_verifier = authentication_verifier
        self._lease_ttl = lease_ttl
        self._lease_max_requests = lease_max_requests

    async def supply_lease(self):
        if self._lease_ttl is not None and self._lease_max_requests is not None:
            frame = LeaseFrame()
            frame.number_of_requests = self._lease_max_requests
            frame.time_to_live = to_milliseconds(self._lease_ttl)
            self.socket.send_frame(frame)

    # noinspection PyAttributeOutsideInit
    async def on_setup(self,
                       data_encoding: bytes,
                       metadata_encoding: bytes):

        if metadata_encoding != WellKnownMimeTypes.MESSAGE_RSOCKET_COMPOSITE_METADATA.value.name:
            raise Exception('Setup frame did not specify composite metadata. required for routing handler')
        else:
            self.data_encoding = data_encoding
            self.metadata_encoding = metadata_encoding
            await super().on_setup(data_encoding, metadata_encoding)

    async def request_channel(self, payload: Payload) -> Tuple[Publisher, Subscriber]:
        try:
            return await self._parse_and_route(payload)
        except Exception as exception:
            return self._error_stream_handler(exception), NullSubscriber()

    async def request_fire_and_forget(self, payload: Payload):
        try:
            await self._parse_and_route(payload)
        except Exception:
            logger().error('Error', exc_info=True)

    async def request_response(self, payload: Payload) -> Future:
        try:
            return await self._parse_and_route(payload)
        except Exception as exception:
            return self._error_future(exception)

    async def request_stream(self, payload: Payload) -> Publisher:
        try:
            return await self._parse_and_route(payload)
        except Exception as exception:
            return self._error_stream_handler(exception)

    def _error_stream_handler(self, exception):
        return ErrorStream(exception)

    def _error_future(self, exception):
        future = asyncio.Future()
        future.set_exception(exception)
        return future

    async def _parse_and_route(self, payload: Payload) -> Union[Future, Publisher, None, Tuple[Publisher, Subscriber]]:
        composite_metadata = CompositeMetadata()
        composite_metadata.parse(payload.metadata)
        route = self._require_route(composite_metadata)
        await self._verify_authentication(route, composite_metadata)
        return await self.router.route(route, payload, composite_metadata)

    def _require_route(self, composite_metadata: CompositeMetadata) -> Optional[str]:
        for item in composite_metadata.items:
            if isinstance(item, RoutingMetadata):
                return item.tags[0].decode()

        raise Exception('No route found in request')

    async def _verify_authentication(self, route: str, composite_metadata: CompositeMetadata):
        if self.authentication_verifier is not None:
            for item in composite_metadata.items:
                if isinstance(item, AuthenticationContent):
                    await self.authentication_verifier(route, item.authentication)
                    return

            raise Exception('Authentication required but not provided')

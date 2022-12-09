from dataclasses import dataclass
from inspect import signature, Parameter
from typing import Callable, Any

from rsocket.exceptions import RSocketUnknownRoute, RSocketEmptyRoute
from rsocket.extensions.composite_metadata import CompositeMetadata
from rsocket.frame import FrameType
from rsocket.frame_helpers import safe_len
from rsocket.payload import Payload
from rsocket.rsocket import RSocket

__all__ = ['RequestRouter']

decorated_method = Callable[[RSocket, Payload, CompositeMetadata], Any]


def decorator_factory(container: dict, route: str):
    def decorator(function: decorated_method):
        if safe_len(route) == 0:
            raise RSocketEmptyRoute(function.__name__)

        if route in container:
            raise KeyError('Duplicate route "%s" already registered', route)

        container[route] = function
        return function

    return decorator


@dataclass
class Handlers:
    response: callable = None
    stream: callable = None
    channel: callable = None
    fire_and_forget: callable = None
    metadata_push: callable = None


class RequestRouter:
    __slots__ = (
        '_channel_routes',
        '_stream_routes',
        '_response_routes',
        '_fnf_routes',
        '_metadata_push',
        '_route_map_by_frame_type',
        '_payload_mapper',
        '_unknown'
    )

    def __init__(self, payload_mapper=lambda cls, _: _):
        self._payload_mapper = payload_mapper
        self._channel_routes = {}
        self._stream_routes = {}
        self._response_routes = {}
        self._fnf_routes = {}
        self._metadata_push = {}

        self._unknown = Handlers()

        self._route_map_by_frame_type = {
            FrameType.REQUEST_CHANNEL: self._channel_routes,
            FrameType.REQUEST_FNF: self._fnf_routes,
            FrameType.REQUEST_STREAM: self._stream_routes,
            FrameType.REQUEST_RESPONSE: self._response_routes,
            FrameType.METADATA_PUSH: self._metadata_push,
        }

    def response(self, route: str):
        return decorator_factory(self._response_routes, route)

    def response_unknown(self):
        def wrapper(function):
            self._unknown.response = function
            return function

        return wrapper

    def stream(self, route: str):
        return decorator_factory(self._stream_routes, route)

    def stream_unknown(self):
        def wrapper(function):
            self._unknown.stream = function
            return function

        return wrapper

    def channel(self, route: str):
        return decorator_factory(self._channel_routes, route)

    def channel_unknown(self):
        def wrapper(function):
            self._unknown.channel = function
            return function

        return wrapper

    def fire_and_forget(self, route: str):
        return decorator_factory(self._fnf_routes, route)

    def fire_and_forget_unknown(self):
        def wrapper(function):
            self._unknown.fire_and_forget = function
            return function

        return wrapper

    def metadata_push(self, route: str):
        return decorator_factory(self._metadata_push, route)

    def metadata_push_unknown(self):
        def wrapper(function):
            self._unknown.metadata_push = function
            return function

        return wrapper

    async def route(self,
                    frame_type: FrameType,
                    route: str,
                    payload: Payload,
                    composite_metadata: CompositeMetadata):

        if route in self._route_map_by_frame_type[frame_type]:
            route_processor = self._route_map_by_frame_type[frame_type][route]
        else:
            route_processor = self._get_unknown_route(frame_type)

        if route_processor is None:
            raise RSocketUnknownRoute(route)

        route_kwargs = self._collect_route_arguments(route_processor,
                                                     payload,
                                                     composite_metadata)

        return await route_processor(**route_kwargs)

    def _collect_route_arguments(self, route_processor, payload, composite_metadata):
        route_signature = signature(route_processor)
        route_kwargs = {}

        for parameter in route_signature.parameters:
            parameter_type = route_signature.parameters[parameter]

            if 'composite_metadata' == parameter:
                route_kwargs['composite_metadata'] = composite_metadata
            else:
                if parameter_type.annotation not in (Payload, parameter_type.empty):
                    payload = self._payload_mapper(parameter_type.annotation, payload)

                route_kwargs[parameter] = payload

        return route_kwargs

    def _get_unknown_route(self, frame_type: FrameType) -> Callable:
        if frame_type == FrameType.REQUEST_RESPONSE:
            return self._unknown.response
        elif frame_type == FrameType.REQUEST_STREAM:
            return self._unknown.stream
        elif frame_type == FrameType.REQUEST_CHANNEL:
            return self._unknown.channel
        elif frame_type == FrameType.REQUEST_FNF:
            return self._unknown.fire_and_forget
        elif frame_type == FrameType.METADATA_PUSH:
            return self._unknown.metadata_push

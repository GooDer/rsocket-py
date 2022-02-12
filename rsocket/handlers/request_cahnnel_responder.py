from rsocket.frame import Frame, RequestChannelFrame
from rsocket.handlers.request_cahnnel_common import RequestChannelCommon


class RequestChannelResponder(RequestChannelCommon):

    def frame_received(self, frame: Frame):
        if isinstance(frame, RequestChannelFrame):
            if self.subscriber.subscription is None:
                self.socket.send_complete(self.stream)
                self.mark_completed_and_finish(sent=True)
            else:
                self.subscriber.subscription.request(frame.initial_request_n)

            if frame.flags_complete:
                self._complete_remote_subscriber()

        else:
            super().frame_received(frame)

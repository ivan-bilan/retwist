import codecs
import json
import re
from typing import Any, Callable, Dict, IO, Optional, Union

try:
    from inspect import iscoroutinefunction
except ImportError:
    # We must be on Python < 3.5!
    def iscoroutinefunction(func):
        # type: (Callable) -> bool
        return False

from twisted.internet.defer import CancelledError, Deferred, ensureDeferred, maybeDeferred
from twisted.internet.error import ConnectError, ConnectionDone, ConnectionLost
from twisted.python import log
from twisted.python.failure import Failure
from twisted.web.error import Error
from twisted.web.resource import Resource
from twisted.web.http import Request, BAD_REQUEST, INTERNAL_SERVER_ERROR
from twisted.web.server import NOT_DONE_YET

from retwist.param_resource import ParamResource


class JsonResource(ParamResource):
    """
    Twisted resource with convenience methods to encode JSON responses. Supports JSONP.
    """

    encoding = "utf-8"
    # Factory function for a UTF-8 stream encoder:
    create_writer = codecs.getwriter(encoding)  # type: Callable[[Any], IO]

    jsonp_callback_re = re.compile(b"^[_a-zA-Z0-9\.$]+$")

    @classmethod
    def json_dump_default(cls, o):
        # type: (Any) -> Any
        """
        Override this to implement custom JSON encoding. Passed to json.dump method.
        :param o: Object which cannot be JSON-serialized.
        :return: JSON-serializable object (or throw TypeError)
        """
        raise TypeError("Can't JSON serialize {}".format(type(o).__name__))

    def json_GET(self, request):
        # type: (Request) -> Dict
        """
        Override this to return JSON data to render.
        :param request: Twisted request.
        """
        raise NotImplementedError()

    def render(self, request):
        # type: (Request) -> Union[int, bytes]
        """
        Before we render this request as normal, parse parameters, and add them to the request! Also, catch any errors
        during parameter parsing, and show them appropriately.
        :param request: Twisted request object
        :return: Byte string or NOT_DONE_YET - see IResource.render
        """
        try:
            request.url_args = self.parse_args(request)
        except Exception as ex:
            self.handle_exception(request)
            return NOT_DONE_YET
        else:
            return Resource.render(self, request)

    def render_GET(self, request):
        # type: (Request) -> int
        """
        Get JSON data from json_GET, and render for the client.
        
        Do not override in sub classes ...
        :param request: Twisted request
        """
        if iscoroutinefunction(self.json_GET):
            coroutine = self.json_GET(request)
            json_def = ensureDeferred(coroutine)  # type: Deferred
        else:
            json_def = maybeDeferred(self.json_GET, request)

        json_def.addCallback(self.send_json_response, request)
        json_def.addErrback(self.handle_failure, request)

        # handle connection failures
        request.notifyFinish().addErrback(self.on_connection_closed, json_def)

        return NOT_DONE_YET

    def response_envelope(self, response, status_code=200, status_message=None):
        # type: (Any, int, Optional[str]) -> Any
        """
        Implement this to transform JSON responses before sending, e.g. by putting HTTP status codes in the response.
        :param response: JSON data about to be sent to client 
        :param status_code: HTTP status code
        :param status_message: Optional status message, e.g. error message
        :return: Wrapped JSON-serializable data
        """
        return response

    def send_json_response(self, response, request, status_code=200, status_message=None):
        # type: (Any, Request, int, Optional[str]) -> None
        """
        Send JSON data to client.
        :param response: JSON-serializable data 
        :param request: Twisted request
        :param status_code: HTTP status code
        :param status_message: Optional status message, e.g. error message
        """
        is_jsonp = len(request.args.get(b"callback", [])) == 1
        if is_jsonp:
            callback = request.args[b"callback"][0]
            if not self.jsonp_callback_re.match(callback):
                del request.args[b"callback"]
                return self.send_json_response("Invalid callback", request, status_code=BAD_REQUEST)
            request.setHeader(b"Content-Type", b"application/javascript; charset=%s" % self.encoding.encode())
            request.write(callback + b"(")
        else:
            request.setHeader(b"Content-Type", b"application/json; charset=%s" % self.encoding.encode())
            request.setResponseCode(status_code)

        response = self.response_envelope(response, status_code=status_code, status_message=status_message)
        stream = JsonResource.create_writer(request)
        json.dump(response, stream, allow_nan=False, default=self.json_dump_default)

        if is_jsonp:
            request.write(b")")

        request.finish()

    def handle_exception(self, request):
        # type: (Request) -> None
        """
        Handle the most recent exception.
        :param request: Twisted request
        """
        self.handle_failure(Failure(), request)

    def handle_failure(self, failure, request):
        # type: (Failure, Request) -> None
        """
        Handle a failure. For connection errors, we do nothing - no chance to send anything. For client errors, we show
        an informative error message. For server errors, we show a generic message, and log the error.
        :param failure: Failure instance
        :param request: Twisted request
        """
        exception = failure.value  # type: Exception

        # Connection errors. This is business as usual on the Internet. We do nothing - we can't reach the client to
        # tell them about it anyways, and it's not worth logging.
        if any(isinstance(exception, exc_type) for exc_type in {
            CancelledError, ConnectError, ConnectionDone, ConnectionLost
        }):
            return

        # Client error - we expose the error message to the client, but don't log anything.
        if isinstance(exception, Error):
            web_error = exception  # type: Error
            status_code = int(web_error.status)
            if 400 <= status_code < 500:
                message = str(exception)
                self.send_error(status_code, message, request)
                return

        # Server error - we don't let the client see any part of the exception, since it might expose internals. But we
        # totally need to log it.
        error_msg = str(exception)
        context = "{} @ {} ({})".format(type(exception).__name__, request.uri.decode(), error_msg)
        log.err(exception, context)

        self.send_error(INTERNAL_SERVER_ERROR, "Server-side error", request)

    def send_error(self, status_code, message, request):
        # type: (int, str, Request) -> None
        """
        Send error message to the client.
        :param status_code: HTTP error code
        :param message: Error message that we want to expose to the client
        :param request: Twisted request
        """
        self.send_json_response(message, request, status_code=status_code)

    def on_connection_closed(self, failure, deferred):
        # type: (Failure, Deferred) -> None
        """
        Handle connection errors.
        :param failure: Twisted failure 
        :param deferred: The async call to json_GET
        """
        deferred.cancel()

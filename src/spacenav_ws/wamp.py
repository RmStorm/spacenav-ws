from __future__ import annotations

import asyncio
import logging
import os
import random
import string
from dataclasses import astuple, dataclass
from enum import IntEnum
from typing import Any, ClassVar

from fastapi import WebSocket

WIRE_LOG_WAMP = os.environ.get("SPACENAV_WS_WIRE_LOG", "0") == "1"
DEFAULT_RPC_TIMEOUT_S = 2.0


def _generate_id(width: int) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choices(alphabet, k=width))


class WampMsgType(IntEnum):
    WELCOME = 0
    PREFIX = 1
    CALL = 2
    CALLRESULT = 3
    CALLERROR = 4
    SUBSCRIBE = 5
    EVENT = 8


@dataclass(frozen=True)
class WampMessage:
    MSG_TYPE: ClassVar[WampMsgType]

    def serialize(self) -> list[Any]:
        return list(astuple(self))

    def serialize_with_msg_id(self) -> list[Any]:
        return [int(self.MSG_TYPE), *self.serialize()]


@dataclass(frozen=True)
class Welcome(WampMessage):
    session_id: str
    version: int
    server_ident: str

    MSG_TYPE: ClassVar[WampMsgType] = WampMsgType.WELCOME


@dataclass(frozen=True)
class Prefix(WampMessage):
    prefix: str
    uri: str

    MSG_TYPE: ClassVar[WampMsgType] = WampMsgType.PREFIX


@dataclass(frozen=True, init=False)
class Call(WampMessage):
    call_id: str
    proc_uri: str
    args: list[Any]

    MSG_TYPE: ClassVar[WampMsgType] = WampMsgType.CALL

    def __init__(self, call_id: str, proc_uri: str, *args: Any):
        object.__setattr__(self, "call_id", call_id)
        object.__setattr__(self, "proc_uri", proc_uri)
        object.__setattr__(self, "args", list(args))

    def serialize(self) -> list[Any]:
        return [self.call_id, self.proc_uri, *self.args]

    @classmethod
    def create(cls, proc_uri: str, *args: Any) -> Call:
        return cls(_generate_id(18), proc_uri, *args)


@dataclass(frozen=True)
class CallResult(WampMessage):
    call_id: str
    result: Any

    MSG_TYPE: ClassVar[WampMsgType] = WampMsgType.CALLRESULT


@dataclass(frozen=True)
class CallError(WampMessage):
    call_id: str
    error_uri: str
    desc: str
    details: Any = None

    MSG_TYPE: ClassVar[WampMsgType] = WampMsgType.CALLERROR


@dataclass(frozen=True)
class Subscribe(WampMessage):
    topic: str

    MSG_TYPE: ClassVar[WampMsgType] = WampMsgType.SUBSCRIBE


@dataclass(frozen=True)
class Event(WampMessage):
    topic: str
    payload: Any

    MSG_TYPE: ClassVar[WampMsgType] = WampMsgType.EVENT


MESSAGE_TYPES_BY_ID: dict[WampMsgType, type[WampMessage]] = {
    Welcome.MSG_TYPE: Welcome,
    Prefix.MSG_TYPE: Prefix,
    Call.MSG_TYPE: Call,
    CallResult.MSG_TYPE: CallResult,
    CallError.MSG_TYPE: CallError,
    Subscribe.MSG_TYPE: Subscribe,
    Event.MSG_TYPE: Event,
}


class WampError(RuntimeError):
    pass


class WampClosedError(WampError):
    pass


class WampRpcTimeoutError(WampError):
    pass


class WampRpcRemoteError(WampError):
    pass


@dataclass
class PendingRpc:
    gate: asyncio.Event
    result: Any = None
    error: BaseException | None = None


def _decode_message(data: Any) -> WampMessage:
    if not isinstance(data, list) or not data:
        raise ValueError(f"Invalid WAMP payload: {data!r}")
    try:
        msg_type = WampMsgType(data[0])
    except ValueError as exc:
        raise ValueError(f"Unknown WAMP message type: {data[0]!r}") from exc

    cls = MESSAGE_TYPES_BY_ID.get(msg_type)
    if cls is None:
        raise ValueError(f"Unsupported WAMP message type: {msg_type!r}")
    return cls(*data[1:])


class WampProtocol:
    def __init__(self, websocket: WebSocket):
        self._socket = websocket
        self._server_id = "snbridge v0.0.1"
        self._session_id = _generate_id(16)
        self._send_lock = asyncio.Lock()
        self.prefixes: dict[str, str] = {}
        self.call_handlers: dict[str, Any] = {}
        self.subscribe_handlers: dict[str, Any] = {}

    async def begin(self):
        await self._socket.accept(subprotocol="wamp")
        await self.send_message(Welcome(self._session_id, 1, self._server_id))

    async def send_message(self, msg: WampMessage):
        payload = msg.serialize_with_msg_id()
        if WIRE_LOG_WAMP:
            logging.debug("sending WAMP message: %s", payload)
        async with self._send_lock:
            await self._socket.send_json(payload)

    async def next_message(self) -> WampMessage:
        payload = await self._socket.receive_json()
        if WIRE_LOG_WAMP:
            logging.debug("received WAMP payload: %s", payload)
        return _decode_message(payload)

    def resolve(self, uri: str) -> str:
        if ":" not in uri:
            return uri
        prefix, suffix = uri.split(":", 1)
        base = self.prefixes.get(prefix)
        return f"{base}{suffix}" if base is not None else uri

    async def run_message_handler(self, msg: WampMessage):
        handler = {
            Prefix.MSG_TYPE: self.handle_prefix,
            Call.MSG_TYPE: self.handle_call,
            Subscribe.MSG_TYPE: self.handle_subscribe,
            CallResult.MSG_TYPE: self.handle_callresult,
            CallError.MSG_TYPE: self.handle_callerror,
        }.get(msg.MSG_TYPE)
        if handler is None:
            logging.warning("Ignoring WAMP message type %s", msg.MSG_TYPE.name)
            return
        await handler(msg)

    async def handle_prefix(self, msg: Prefix):
        self.prefixes[msg.prefix] = msg.uri

    async def handle_call(self, msg: Call):
        rpc = self.call_handlers.get(self.resolve(msg.proc_uri))
        if rpc is None:
            await self.send_message(CallError(msg.call_id, "wamp.error.not_found", f"RPC {msg.proc_uri!r} not registered"))
            return
        result = await rpc(*msg.args)
        await self.send_message(CallResult(msg.call_id, result))

    async def handle_subscribe(self, msg: Subscribe):
        handler = self.subscribe_handlers.get(self.resolve(msg.topic))
        if handler is None:
            logging.warning("Ignoring unknown subscription topic %s", msg.topic)
            return
        await handler(msg)

    async def handle_callresult(self, msg: CallResult):
        logging.warning("Ignoring unsolicited callresult for %s", msg.call_id)

    async def handle_callerror(self, msg: CallError):
        logging.warning("Ignoring unsolicited callerror for %s", msg.call_id)


class WampSession:
    def __init__(self, websocket: WebSocket, rpc_timeout_s: float = DEFAULT_RPC_TIMEOUT_S):
        self.wamp = WampProtocol(websocket)
        self.rpc_timeout_s = rpc_timeout_s
        self.pending_rpcs: dict[str, PendingRpc] = {}
        self.closed = False
        self.close_error: BaseException | None = None
        self.wamp.handle_callresult = self.handle_callresult
        self.wamp.handle_callerror = self.handle_callerror

    async def close(self, exc: BaseException | None = None):
        if self.closed:
            return
        self.closed = True
        self.close_error = exc
        error = exc or WampClosedError("WAMP session closed")
        for rpc in self.pending_rpcs.values():
            rpc.error = error
            rpc.gate.set()

    async def start_wamp_message_stream(self):
        try:
            while True:
                await self.wamp.run_message_handler(await self.wamp.next_message())
        except BaseException as exc:
            await self.close(exc)
            raise

    async def client_rpc(self, controller_uri: str, method: str, *args: Any):
        if self.closed:
            raise WampClosedError("WAMP session closed") from self.close_error

        call = Call.create(method, "", *args)
        rpc = PendingRpc(gate=asyncio.Event())
        self.pending_rpcs[call.call_id] = rpc
        try:
            await self.wamp.send_message(Event(controller_uri, call.serialize_with_msg_id()))
            try:
                await asyncio.wait_for(rpc.gate.wait(), timeout=self.rpc_timeout_s)
            except TimeoutError as exc:
                raise WampRpcTimeoutError(f"WAMP RPC timed out after {self.rpc_timeout_s}s: {call.proc_uri}") from exc

            if rpc.error is not None:
                raise rpc.error
            return rpc.result
        except BaseException as exc:
            if not isinstance(exc, (WampRpcRemoteError, WampRpcTimeoutError, WampClosedError)) and not self.closed:
                await self.close(exc)
            raise
        finally:
            self.pending_rpcs.pop(call.call_id, None)

    async def handle_callresult(self, msg: CallResult):
        rpc = self.pending_rpcs.get(msg.call_id)
        if rpc is None:
            logging.warning("Ignoring unexpected WAMP callresult for %s", msg.call_id)
            return
        rpc.result = msg.result
        rpc.gate.set()

    async def handle_callerror(self, msg: CallError):
        rpc = self.pending_rpcs.get(msg.call_id)
        if rpc is None:
            logging.warning("Ignoring unexpected WAMP callerror for %s", msg.call_id)
            return
        rpc.error = WampRpcRemoteError(f"{msg.error_uri}: {msg.desc}")
        rpc.gate.set()

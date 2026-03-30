import asyncio

import pytest

from spacenav_ws.wamp import (
    CallError,
    CallResult,
    Event,
    Prefix,
    WampClosedError,
    WampProtocol,
    WampRpcRemoteError,
    WampRpcTimeoutError,
    WampSession,
)


class DummyWebSocket:
    def __init__(self):
        self.sent = []
        self.recv = asyncio.Queue()

    async def accept(self, subprotocol=None):
        return None

    async def send_json(self, payload):
        self.sent.append(payload)

    async def receive_json(self):
        item = await self.recv.get()
        if isinstance(item, BaseException):
            raise item
        return item


def test_client_rpc_times_out():
    async def main():
        ws = DummyWebSocket()
        session = WampSession(ws, rpc_timeout_s=0.01)
        with pytest.raises(WampRpcTimeoutError):
            await session.client_rpc("controller", "self:read", "view.affine")

    asyncio.run(main())


def test_client_rpc_surfaces_remote_error():
    async def main():
        ws = DummyWebSocket()
        session = WampSession(ws, rpc_timeout_s=0.1)
        task = asyncio.create_task(session.client_rpc("controller", "self:read", "view.affine"))
        await asyncio.sleep(0)
        call_id = next(iter(session.pending_rpcs))
        await session.handle_callerror(CallError(call_id, "wamp.error.not_found", "missing"))
        with pytest.raises(WampRpcRemoteError):
            await task

    asyncio.run(main())


def test_client_rpc_returns_callresult():
    async def main():
        ws = DummyWebSocket()
        session = WampSession(ws, rpc_timeout_s=0.1)
        task = asyncio.create_task(session.client_rpc("controller", "self:read", "view.affine"))
        await asyncio.sleep(0)
        call_id = next(iter(session.pending_rpcs))
        await session.handle_callresult(CallResult(call_id, [1, 2, 3]))
        assert await task == [1, 2, 3]
        assert len(ws.sent) == 1
        assert ws.sent[0][0] == Event.MSG_TYPE

    asyncio.run(main())


def test_disconnect_fails_inflight_rpcs():
    async def main():
        ws = DummyWebSocket()
        session = WampSession(ws, rpc_timeout_s=1.0)
        rpc_task = asyncio.create_task(session.client_rpc("controller", "self:read", "view.affine"))
        await asyncio.sleep(0)
        stream_task = asyncio.create_task(session.start_wamp_message_stream())
        await ws.recv.put(RuntimeError("socket closed"))
        with pytest.raises(RuntimeError, match="socket closed"):
            await stream_task
        with pytest.raises(RuntimeError, match="socket closed"):
            await rpc_task

    asyncio.run(main())


def test_wamp_protocol_resolve_leaves_unprefixed_uri_unchanged():
    protocol = WampProtocol(DummyWebSocket())
    assert protocol.resolve("plain-uri") == "plain-uri"
    asyncio.run(protocol.handle_prefix(Prefix("self", "wss://example/")))
    assert protocol.resolve("self:update") == "wss://example/update"


def test_closed_session_rejects_new_rpcs():
    async def main():
        ws = DummyWebSocket()
        session = WampSession(ws)
        await session.close()
        with pytest.raises(WampClosedError):
            await session.client_rpc("controller", "self:read", "view.affine")

    asyncio.run(main())

import asyncio
import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from urllib.parse import quote

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from services.volleyball_physics import DEFAULT_SETTINGS, GROUND, Match, resolve_net, validate_settings

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://volleyball.test"


class PhysicsTests(unittest.TestCase):
    def test_settings_are_strict_and_partial_settings_use_defaults(self):
        self.assertEqual(validate_settings({"netHeight": 240})["speed1"], 100)
        for raw in [{"speed1": True}, {"speed1": 26}, {"speed1": 10**500}, {"gravity": float("nan")},
                    {"winScore": 0}, {"maxTouches": 3.5}, {"winByTwo": "true"}, {"__proto__": {}}, []]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_settings(raw)

    def test_movement_jump_and_side_boundaries(self):
        m = Match({"speed1": 200, "speed2": 25, "jump1": 150})
        m.start()
        for _ in range(10):
            m.step([2, 1])
        self.assertGreater(m.players[0]["vx"], abs(m.players[1]["vx"])*3)
        m.step([4, 0])
        self.assertLess(m.players[0]["y"], GROUND)
        for _ in range(400):
            m.step([2, 1])
        self.assertLessEqual(m.players[0]["x"], m.net["x"]-45)
        self.assertGreaterEqual(m.players[1]["x"], m.net["x"]+45)

    def test_net_collision_and_win_by_two(self):
        m = Match({"ballSpeed": 200, "airDrag": 0, "winByTwo": True})
        m.start()
        m.serving = False
        m.ball.update(x=m.net["x"]-m.net["width"]-m.ball["radius"]-10, y=m.net["y"]+50, vx=160, vy=0)
        m.move_ball()
        self.assertLess(m.ball["vx"], 0)
        m.score = [4, 4]
        m.point(1)
        self.assertEqual(m.phase, "playing")
        m.point(1)
        self.assertEqual((m.phase, m.score), ("finished", [6, 4]))

    def test_touch_limit_and_unlimited_mode(self):
        for max_touches in (0, 3):
            m = Match({"maxTouches": max_touches})
            m.start()
            m.possession, m.touches = 1, 3
            m.ball.update(x=m.players[0]["x"], y=GROUND-150)
            m.step()
            self.assertEqual(m.score[1], 0 if max_touches == 0 else 1)

    def test_small_ball_cannot_be_pushed_through_net_by_player(self):
        for side in (-1, 1):
            m = Match({"ballSize": 50, "netWidth": 4, "maxTouches": 0})
            m.start()
            m.serving = False
            player = m.players[0 if side == -1 else 1]
            player.update(x=m.net["x"]+side*45, y=GROUND)
            m.ball.update(x=m.net["x"]+side*27, y=500, vx=-side*4, vy=0)
            m.step()
            self.assertGreater((m.ball["x"]-m.net["x"])*side, m.ball["radius"]+m.net["width"])
            self.assertGreater(m.ball["vx"]*side, 0)
            self.assertEqual(m.combo, 1)

    def test_net_sweep_blocks_fast_motion_and_round_cap_but_allows_clearance(self):
        for size in (50, 100, 150):
            for width in (4, 24):
                m = Match({"ballSize": size, "netWidth": width})
                b, n = m.ball, m.net
                for side in (-1, 1):
                    old_x = n["x"]+side*250
                    b.update(x=n["x"]-side*250, y=n["y"]+80, vx=-side*10000, vy=0)
                    self.assertTrue(resolve_net(b, n, old_x, b["y"]))
                    self.assertGreater((b["x"]-n["x"])*side, b["radius"]+width)
                    self.assertGreater(b["vx"]*side, 0)
                b.update(x=n["x"], y=n["y"]+200, vx=0, vy=10000)
                self.assertTrue(resolve_net(b, n, n["x"], n["y"]-250))
                self.assertLess(b["y"], n["y"]-width-b["radius"])
                self.assertLess(b["vy"], 0)
                y = n["y"]-width-b["radius"]-1
                b.update(x=n["x"]+250, y=y, vx=10000, vy=0)
                self.assertFalse(resolve_net(b, n, n["x"]-250, y))

    def test_weak_jump_reaches_serve_and_extreme_physics_is_finite(self):
        m = Match({"jump1": 50, "gravity": 180, "serveHeight": 420})
        m.start()
        for _ in range(60):
            m.step([4, 0])
            if not m.serving:
                break
        self.assertFalse(m.serving)
        m = Match({"gravity": 30, "ballSpeed": 200, "hitPower": 200, "speed1": 250, "speed2": 250,
                   "jump1": 180, "jump2": 180, "netHeight": 460, "netWidth": 4, "ballSize": 50})
        m.start()
        m.target = 100000
        for i in range(10000):
            m.step([6 if i % 40 < 25 else 1, 5 if i % 60 < 40 else 2])
            self.assertTrue(all(math.isfinite(p[k]) for p in [m.ball, *m.players] for k in ("x", "y", "vx", "vy")))


class Peer:
    def __init__(self, ws):
        self.ws, self.packets = ws, []

    async def send(self, **packet):
        await self.ws.send(json.dumps(packet))

    async def wait(self, kind, predicate=lambda p: True):
        for i, packet in enumerate(self.packets):
            if packet["type"] == kind and predicate(packet):
                return self.packets.pop(i)

        async def receive():
            while True:
                packet = json.loads(await self.ws.recv())
                if packet["type"] == kind and predicate(packet):
                    return packet
                self.packets.append(packet)
        return await asyncio.wait_for(receive(), 5)


class OnlineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Every test uses a fresh, real Uvicorn server, so limiter state is real
        # and tests need neither production credentials nor PostgreSQL.
        self.log = tempfile.TemporaryFile(mode="w+")
        env = os.environ.copy()
        env.update({"TURN_SECRET": "test-only", "POSTGRESQL_HOST": "127.0.0.1", "POSTGRESQL_USER": "test",
                    "POSTGRESQL_PASSWORD": "test-only", "SMTP_PASSWORD": "", "ALLOWED_ORIGINS": json.dumps([ORIGIN]),
                    "VOLLEYBALL_ENABLED": "true", "VOLLEYBALL_MAX_ROOMS": "1", "VOLLEYBALL_MAX_CONNECTIONS_PER_IP": "6"})
        for suffix in ("", "_APEX", "_REAL_BABY", "_AMA_LIVING", "_WAYFS", "_FERALRU", "_SHELWEAR"):
            env["POSTGRESQL_DATABASE"+suffix] = "unused_test_database"
        self.test_env = env
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "tests.volleyball_fixture:create_app", "--factory", "--fd", str(sock.fileno()),
                 "--ws", "websockets", "--log-level", "warning"], cwd=ROOT, env=env,
                pass_fds=(sock.fileno(),), stdout=self.log, stderr=subprocess.STDOUT,
            )
        self.base, self.url, self.peers = f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}/volleyball/ws", []
        # A cold Python import can exceed three seconds in the cloud workspace.
        # Wait for actual readiness rather than a fixed count of quick refusals.
        deadline = time.monotonic()+15
        last_error = None
        while time.monotonic() < deadline:
            try:
                response = await asyncio.to_thread(urlopen, self.base+"/volleyball/healthz", timeout=.2)
                response.close()
                return
            except (OSError, URLError) as error:
                last_error = repr(error)
                if self.proc.poll() is not None:
                    self.log.seek(0)
                    self.fail(self.log.read())
                await asyncio.sleep(.05)
        self.proc.terminate()
        await asyncio.to_thread(self.proc.wait, timeout=5)
        self.log.seek(0)
        self.fail(f"Game startup timed out: {last_error}\n"+self.log.read())

    async def asyncTearDown(self):
        for peer in self.peers:
            await peer.ws.close()
        self.proc.terminate()
        await asyncio.to_thread(self.proc.wait, timeout=10)
        self.log.seek(0)
        output = self.log.read()
        self.log.close()
        self.assertNotIn("Traceback", output)

    async def peer(self):
        ws = await connect(self.url, origin=ORIGIN, subprotocols=["volley-v1"], compression=None, proxy=None)
        peer = Peer(ws)
        self.peers.append(peer)
        self.assertEqual((await peer.wait("hello"))["protocol"], 1)
        self.assertEqual(ws.subprotocol, "volley-v1")
        return peer

    async def pair(self):
        host = await self.peer()
        await host.send(type="create", color=0)
        owner = await host.wait("joined")
        guest = await self.peer()
        await guest.send(type="join", code=owner["code"], color=1)
        visitor = await guest.wait("joined")
        return host, guest, owner, visitor

    async def test_two_players_share_movement_score_and_resume(self):
        host, guest, owner, visitor = await self.pair()
        self.assertRegex(owner["code"], r"^[1-9][0-9]{3}$")
        await host.send(type="start")
        first = await host.wait("state", lambda p: p["state"]["phase"] == "playing")
        await host.send(type="input", seq=1, mask=6)
        await guest.send(type="input", seq=1, mask=1)
        state = await host.wait("state", lambda p: p["state"]["tick"] > first["state"]["tick"]+3)
        self.assertEqual(len(state["inputs"]), 2)
        self.assertGreaterEqual(state["inputs"][0]["seq"], 1)
        self.assertGreaterEqual(state["inputs"][1]["seq"], 1)
        other = await guest.wait("state", lambda p: p["seq"] == state["seq"])
        self.assertEqual(state["state"], other["state"])
        self.assertGreater(state["state"]["players"][0]["x"], 275)
        self.assertLess(state["state"]["players"][1]["x"], 825)
        # The jump serves the ball. Let authoritative physics award a point.
        point = await host.wait("state", lambda p: sum(p["state"]["score"]) > 0)
        point2 = await guest.wait("state", lambda p: p["seq"] == point["seq"])
        self.assertEqual(point["state"]["score"], point2["state"]["score"])
        await guest.ws.close()
        await host.wait("room", lambda p: p["room"]["phase"] == "paused" and not p["room"]["players"][1]["connected"])
        impostor = await self.peer()
        await impostor.send(type="resume", code=owner["code"], token="a"*43)
        self.assertEqual((await impostor.wait("error"))["code"], "SESSION_EXPIRED")
        restored = await self.peer()
        await restored.send(type="resume", code=owner["code"], token=visitor["token"])
        self.assertEqual((await restored.wait("joined"))["role"], "guest")
        await host.send(type="pause", paused=False)
        await restored.wait("state", lambda p: p["state"]["phase"] == "playing")
        await host.send(type="leave")
        self.assertEqual((await restored.wait("closed"))["code"], "HOST_LEFT")

    async def test_owner_permissions_settings_and_third_player(self):
        host, guest, owner, visitor = await self.pair()
        for kind in ["start", "restart", "kick", "extend"]:
            await guest.send(type=kind)
            self.assertEqual((await guest.wait("error"))["code"], "NOT_HOST")
        await guest.send(type="settings", settings={"speed1": 250})
        self.assertEqual((await guest.wait("error"))["code"], "NOT_HOST")
        await guest.send(type="color", color=4)
        await host.wait("room", lambda p: p["room"]["players"][1] and p["room"]["players"][1]["color"] == 4)
        await host.send(type="settings", settings={"netHeight": 240, "speed1": 150})
        await guest.wait("room", lambda p: p["room"]["settings"]["speed1"] == 150)
        third = await self.peer()
        await third.send(type="join", code=owner["code"], color=2)
        self.assertEqual((await third.wait("error"))["code"], "ROOM_UNAVAILABLE")
        self.assertNotIn(owner["token"], json.dumps(visitor))
        self.assertNotIn(visitor["token"], json.dumps(owner))
        await host.send(type="kick")
        self.assertEqual((await guest.wait("closed"))["code"], "KICKED")

    async def test_host_arena_view_is_shared_and_resize_does_not_reset_physics(self):
        host = await self.peer()
        initial = {"width": 1008, "height": 420}
        await host.send(type="create", color=0, arenaView=initial)
        owner = await host.wait("joined")
        self.assertEqual(owner["room"]["arenaView"], initial)
        guest = await self.peer()
        await guest.send(type="join", code=owner["code"], color=1)
        visitor = await guest.wait("joined")
        self.assertEqual(visitor["room"]["arenaView"], initial)
        await guest.send(type="arena_view", view={"width": 568, "height": 320})
        self.assertEqual((await guest.wait("error"))["code"], "NOT_HOST")
        await host.send(type="start")
        before = await host.wait("state", lambda p: p["state"]["phase"] == "playing")
        await host.send(type="input", seq=1, mask=2)
        changed = {"width": 836, "height": 393}
        await host.send(type="arena_view", view=changed)
        layout = await guest.wait("room", lambda p: p["room"]["arenaView"] == changed)
        self.assertEqual(layout["room"]["settings"], visitor["room"]["settings"])
        after = await guest.wait("state", lambda p: p["state"]["tick"] > before["state"]["tick"]+5)
        self.assertEqual(after["state"]["phase"], "playing")
        self.assertEqual(after["state"]["round"], before["state"]["round"])
        self.assertEqual(after["state"]["score"], before["state"]["score"])
        self.assertGreater(after["state"]["players"][0]["x"], before["state"]["players"][0]["x"])
        self.assertEqual(after["inputs"][0], {"seq": 1, "mask": 2})
        await host.ws.close()
        await guest.wait("room", lambda p: not p["room"]["players"][0]["connected"])
        restored = await self.peer()
        await restored.send(type="resume", code=owner["code"], token=owner["token"])
        self.assertEqual((await restored.wait("joined"))["room"]["arenaView"], changed)

    async def test_arena_view_rejects_invalid_dimensions_and_unknown_fields(self):
        # Rejected creates must not allocate a room or bypass strict validation.
        invalid = [{"width": 0, "height": 750}, {"width": True, "height": 750},
                   {"width": 1000.5, "height": 750}, {"width": 1000, "height": 16385},
                   {"width": 1000}, {"width": 1000, "height": 750, "courtWidth": 900}, []]
        for index, view in enumerate(invalid):
            if index % 2 == 0:
                if index:
                    await peer.ws.close()
                peer = await self.peer()
            await peer.send(type="create", color=0, arenaView=view)
            self.assertEqual((await peer.wait("error"))["code"], "INVALID_MESSAGE")
        await peer.send(type="create", color=0)
        created = await peer.wait("joined")
        self.assertEqual(created["room"]["arenaView"], {"width": DEFAULT_SETTINGS["courtWidth"]+48, "height": 750})
        await peer.send(type="arena_view", view={"width": 1000, "height": 0})
        self.assertEqual((await peer.wait("error"))["code"], "INVALID_MESSAGE")

    async def test_wrong_origin_and_protocol_rejected_before_upgrade(self):
        for origin, protocols in [("https://evil.example", ["volley-v1"]), (None, ["volley-v1"]), (ORIGIN, None)]:
            with self.subTest(origin=origin, protocols=protocols), self.assertRaises(InvalidStatus) as err:
                async with connect(self.url, origin=origin, subprotocols=protocols, proxy=None):
                    self.fail("Unexpected upgrade")
            self.assertEqual(err.exception.response.status_code, 403)

    async def test_custom_colors_are_shared_independent_validated_and_resumed(self):
        host = await self.peer()
        await host.send(type="create", color="#Ab19E3")
        owner = await host.wait("joined")
        guest = await self.peer()
        await guest.send(type="join", code=owner["code"], color="#14B8A6")
        visitor = await guest.wait("joined")
        self.assertEqual([p["color"] for p in visitor["room"]["players"]], ["#ab19e3", "#14b8a6"])
        await guest.send(type="color", color="#F5A623")
        await host.wait("room", lambda p: (p["room"]["players"][1] or {}).get("color") == "#f5a623")
        for color in ("red", "#fff"):
            await guest.send(type="color", color=color)
            self.assertEqual((await guest.wait("error"))["code"], "INVALID_MESSAGE")
        await guest.ws.close()
        await host.wait("room", lambda p: p["room"]["players"][1] and not p["room"]["players"][1]["connected"])
        restored = await self.peer()
        await restored.send(type="resume", code=owner["code"], token=visitor["token"])
        colors = [p["color"] for p in (await restored.wait("joined"))["room"]["players"]]
        self.assertEqual(colors, ["#ab19e3", "#f5a623"])
        await restored.send(type="color", color=2)
        await host.wait("room", lambda p: (p["room"]["players"][1] or {}).get("color") == 2)

    async def test_background_catalog_serves_only_listed_images(self):
        with urlopen(self.base+"/volleyball/online/backgrounds.json") as response:
            catalog = json.load(response)
        self.assertEqual(len(catalog), 7)
        self.assertEqual(len({entry["id"] for entry in catalog}), 7)
        for entry in catalog:
            with urlopen(self.base+"/volleyball/online/assets/"+quote(entry["file"])) as response:
                self.assertEqual(response.headers["Content-Type"], "image/png")
                self.assertEqual(response.read(8), b"\x89PNG\r\n\x1a\n")
            with urlopen(self.base+"/volleyball/online/"+entry["thumbnail"]) as response:
                self.assertEqual(response.headers["Content-Type"], "image/webp")
                signature=response.read(12)
                self.assertEqual(signature[:4], b"RIFF")
                self.assertEqual(signature[8:], b"WEBP")
                self.assertLess(int(response.headers["Content-Length"]), 30000)
        for path in ("assets/missing.png", "assets/../config.py", "assets/.env", "assets/%2e%2e/%2e%2e/config.py"):
            with self.assertRaises(HTTPError) as err:
                urlopen(self.base+"/volleyball/online/"+path)
            self.assertEqual(err.exception.code, 404)
            err.exception.close()

    async def test_bad_packets_and_payload_limits(self):
        for payload, code in [("x"*2049, 1009), (b"binary", 1003)]:
            peer = await self.peer()
            await peer.ws.send(payload)
            with self.assertRaises(ConnectionClosed) as err:
                await peer.ws.recv()
            self.assertEqual(err.exception.rcvd.code, code)
        peer = await self.peer()
        for raw in ["not-json", '{"type":"input","mask":2,"seq":true}', '{"type":"input","seq":1,"mask":2,"x":9999}']:
            await peer.ws.send(raw)
            self.assertEqual((await peer.wait("error"))["code"], "INVALID_MESSAGE")
        with self.assertRaises(ConnectionClosed) as err:
            await peer.ws.recv()
        self.assertEqual(err.exception.rcvd.code, 1008)

    async def test_room_cap_and_code_guess_limit(self):
        owner = await self.peer()
        await owner.send(type="create", color=0)
        created = await owner.wait("joined")
        missing_code = "1111" if created["code"] != "1111" else "2222"
        other = await self.peer()
        await other.send(type="create", color=1)
        self.assertEqual((await other.wait("error"))["code"], "SERVER_FULL")
        for _ in range(8):
            await other.send(type="join", code=missing_code, color=1)
            reply = await other.wait("error")
            self.assertEqual(reply["code"], "ROOM_UNAVAILABLE")
        await other.send(type="join", code="2222", color=1)
        self.assertEqual((await other.wait("error"))["code"], "RATE_LIMIT")

    async def test_public_assets_and_private_file_denial(self):
        for path in ["/volleyball/online/", "/volleyball/online", "/volleyball/online/config.js", "/volleyball/online/online.js",
                     "/volleyball/online/online.css", "/volleyball/online/mobile.css", "/volleyball/online/shared/prediction.js",
                     "/volleyball/online/shared/physics.js", "/volleyball/online/shared/settings.js", "/volleyball/online/shared/snapshots.js", "/volleyball/online/shared/balls.js"]:
            with urlopen(self.base+path) as response:
                self.assertEqual(response.status, 200)
                body = response.read().decode()
                if path.endswith("config.js"):
                    self.assertIn("/volleyball/ws", body)
                    self.assertNotIn("5.42.124.68", body)
        for path in ["/volleyball/online/.env", "/volleyball/online/server/app.js", "/volleyball/online/%2e%2e/config.py"]:
            with self.assertRaises(HTTPError) as err:
                urlopen(self.base+path)
            self.assertEqual(err.exception.code, 404)
            err.exception.close()

    async def test_network_round_trips_are_measured_for_both_players(self):
        host, guest, _, _ = await self.pair()
        await host.send(type="ping", id=100)
        await guest.send(type="ping", id=200)
        self.assertTrue((await host.wait("pong"))["probe"])
        await guest.wait("pong")
        await guest.send(type="pong_ack", id=100)  # another player's ID cannot set its RTT
        await asyncio.sleep(.03)
        await host.send(type="pong_ack", id=100)
        await guest.send(type="pong_ack", id=200)
        packet = await host.wait("network", lambda p: all(v is not None for v in p["rtt"]))
        self.assertGreaterEqual(min(packet["rtt"]), 25)
        self.assertLess(max(packet["rtt"]), 1000)
        self.assertGreaterEqual(packet["serverLag"], 0)
        self.assertEqual((await guest.wait("network", lambda p: all(v is not None for v in p["rtt"])))['rtt'], packet['rtt'])
        await host.send(type="start")
        state = await host.wait("state", lambda p: p["state"]["phase"] == "playing")
        self.assertIsInstance(state["serverTime"], float)

    async def test_slow_writer_retains_only_latest_snapshot_and_reliable_controls(self):
        code = '''
from services.volleyball import VolleyballServer, Connection, SNAPSHOT
server = VolleyballServer()
conn = Connection(None, "127.0.0.1")
server.queue_text(conn, "room-created")
for seq in range(1000):
    server.queue_text(conn, str(seq), latest=True)
server.queue_text(conn, "pause")
assert conn.queue.qsize() == 3
assert not conn.closing
assert conn.queue.get_nowait() == "room-created"
assert conn.queue.get_nowait() is SNAPSHOT
assert conn.latest_state == "999"
assert conn.queue.get_nowait() == "pause"
'''
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", code], cwd=ROOT,
                                         env=self.test_env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_shop_http_and_smtp_do_not_freeze_game_simulation(self):
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "tests.check_tilda_nonblocking"],
                                         cwd=ROOT, env=self.test_env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

"""Measure application RTT, input acknowledgements and snapshot delivery for two players."""

import argparse
import asyncio
import json
import math
import statistics
import time
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


def summary(values):
    values = sorted(values)
    return dict(mean=round(statistics.mean(values), 2), p95=round(values[min(len(values)-1, int(len(values)*.95))], 2), maximum=round(max(values), 2)) if values else None


async def measure(origin, duration):
    parsed = urlsplit(origin)
    url = ('wss' if parsed.scheme == 'https' else 'ws')+'://'+parsed.netloc+'/volleyball/ws'
    proxy = None if parsed.hostname in {'localhost', '127.0.0.1', '::1'} else True
    sockets = [await connect(url, origin=origin, subprotocols=['volley-v1'], compression=None, proxy=proxy) for _ in range(2)]
    async def until(ws, kind):
        while True:
            packet = json.loads(await asyncio.wait_for(ws.recv(), 5))
            if packet['type'] == 'error':
                raise RuntimeError(packet['code'])
            if packet['type'] == kind:
                return packet
    try:
        for ws in sockets:
            await until(ws, 'hello')
        await sockets[0].send(json.dumps(dict(type='create',color=0)))
        room = await until(sockets[0], 'joined')
        await sockets[1].send(json.dumps(dict(type='join',code=room['code'],color=1)))
        await until(sockets[1], 'joined')
        await sockets[0].send(json.dumps(dict(type='start')))
        results = [dict(times=[], rtt=[], inputs={}, ack=[], bytes=0, pings={}) for _ in sockets]
        started = time.monotonic()
        async def read(index, ws):
            record = results[index]
            try:
                async for raw in ws:
                    now = time.monotonic()
                    record['bytes'] += len(raw.encode())
                    packet = json.loads(raw)
                    if packet['type'] == 'state':
                        record['times'].append(now)
                        acknowledged = packet.get('inputs', [{},{}])[index].get('seq', -1)
                        for seq, sent in list(record['inputs'].items()):
                            if seq <= acknowledged:
                                record['ack'].append((now-sent)*1000)
                                del record['inputs'][seq]
                    elif packet['type'] == 'pong':
                        record['rtt'].append((now-record['pings'].pop(packet['id']))*1000)
                        if packet.get('probe'):
                            await ws.send(json.dumps(dict(type='pong_ack',id=packet['id'])))
            except ConnectionClosed:
                pass
        async def write(index, ws):
            record, seq, ping_at = results[index], 0, 0
            while time.monotonic()-started < duration:
                now = time.monotonic()
                record['inputs'][seq] = now
                await ws.send(json.dumps(dict(type='input',mask=0,seq=seq)))
                seq += 1
                if now >= ping_at:
                    record['pings'][seq] = now
                    await ws.send(json.dumps(dict(type='ping',id=seq)))
                    ping_at = now+1
                await asyncio.sleep(1/30)
        readers = [asyncio.create_task(read(i,ws)) for i,ws in enumerate(sockets)]
        await asyncio.gather(*(write(i,ws) for i,ws in enumerate(sockets)))
        await sockets[0].send(json.dumps(dict(type='leave')))
        await asyncio.gather(*readers)
        output=[]
        for i, record in enumerate(results):
            stamps=record['times']
            gaps=[(b-a)*1000 for a,b in zip(stamps,stamps[1:])]
            output.append(dict(role='host' if i==0 else 'guest', rtt_ms=summary(record['rtt']), input_ack_ms=summary(record['ack']),
                               updates_per_second=round((len(stamps)-1)/(stamps[-1]-stamps[0]),2), snapshot_gap_ms=summary(gaps),
                               received_kib_per_second=round(record['bytes']/duration/1024,2)))
        print(json.dumps(output,ensure_ascii=False,indent=2))
        return output
    finally:
        for ws in sockets:
            await ws.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('origin',nargs='?',default='http://127.0.0.1:8000')
    parser.add_argument('--seconds',type=float,default=6)
    args=parser.parse_args()
    if not math.isfinite(args.seconds) or not 2 <= args.seconds <= 60:
        parser.error('--seconds must be between 2 and 60')
    asyncio.run(measure(args.origin.rstrip('/'),args.seconds))

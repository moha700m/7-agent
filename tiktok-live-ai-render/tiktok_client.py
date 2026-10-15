"""One explicit asyncio loop per connection; no dependency on private loop attributes."""
import asyncio
import random
from TikTokLive import TikTokLiveClient
from TikTokLive.events import (ConnectEvent, DisconnectEvent, CommentEvent, JoinEvent,
    GiftEvent, FollowEvent, ShareEvent, LikeEvent)


def backoff(attempt):
    return min(30, (2,5,10,20,30)[min(max(0,attempt-1),4)]+random.uniform(0,.8))


def run_client(host, username, stop):
    while not stop.is_set():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = None
        try:
            client = TikTokLiveClient(unique_id=f"@{username}")
            with host.lock:
                host.client, host.client_loop = client, loop

            def user_fields(event):
                user = getattr(event,"user",None)
                uid = str(getattr(user,"unique_id","") or getattr(user,"id","") or "anonymous")
                return dict(username=uid, name=str(getattr(user,"nickname","") or uid)[:80])

            @client.on(ConnectEvent)
            async def connected(_):
                with host.lock:
                    host.tiktok = "LIVE"
                    host.room_id = client.room_id
                    host.reconnect_attempts = 0
                host.publish("status","TikTok LIVE متصل")

            @client.on(DisconnectEvent)
            async def disconnected(_):
                with host.lock:
                    host.tiktok = "RECONNECTING" if not stop.is_set() else "OFFLINE"

            for cls, kind in [(CommentEvent,"comment"),(JoinEvent,"join"),(GiftEvent,"gift"),
                              (FollowEvent,"follow"),(ShareEvent,"share"),(LikeEvent,"like")]:
                def callback_factory(event_kind):
                    async def callback(event):
                        if stop.is_set():
                            return
                        row = dict(type=event_kind, **user_fields(event), text="")
                        msg_id = getattr(event,"msg_id",None)
                        if msg_id:
                            row["source_id"] = f"{event_kind}:{msg_id}"
                        if event_kind=="comment":
                            row["text"] = str(getattr(event,"comment","") or "")[:1000]
                        elif event_kind=="gift":
                            gift = getattr(event,"gift",None)
                            if getattr(gift,"streakable",False) and getattr(event,"streaking",False):
                                return
                            count = max(1,int(getattr(event,"repeat_count",1) or 1))
                            row.update(text=f'{count}x {getattr(gift,"name","هدية")}', gift_count=count,
                                gift_value=max(0,int(getattr(gift,"diamond_count",0) or 0))*count,
                                streak_finished=bool(getattr(gift,"streakable",False)))
                        elif event_kind=="like":
                            row["count"] = max(1,int(getattr(event,"count",1) or 1))
                        host.ingest(row)
                    return callback
                client.add_listener(cls, callback_factory(kind))

            async def connect():
                await client.connect(process_connect_events=False)

            with host.lock:
                host.connection_task = loop.create_task(connect())
                task = host.connection_task
            if not stop.is_set():
                loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
        except Exception:
            with host.lock:
                host.last_error = "تعذر اتصال TikTok؛ إعادة المحاولة بمهلة تدريجية"
            host.publish("error",host.last_error)
        finally:
            if client:
                try:
                    loop.run_until_complete(asyncio.wait_for(client.disconnect(close_client=True),3))
                except (Exception,asyncio.CancelledError):
                    pass
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending,return_exceptions=True))
            loop.close()
            with host.lock:
                host.client = host.client_loop = host.connection_task = None
                host.room_id = None
        if stop.is_set():
            break
        with host.lock:
            host.tiktok = "RECONNECTING"
            host.reconnect_attempts += 1
            attempt = host.reconnect_attempts
        host.metrics.inc("reconnects")
        stop.wait(backoff(attempt))
    with host.lock:
        host.tiktok = "OFFLINE"

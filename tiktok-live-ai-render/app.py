"""HTTP compatibility layer. Host lifecycle and decisions live in runtime.py."""
import atexit
import hmac
import os
import re
import threading
import time
from collections import defaultdict, deque
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from characters import CHARACTER_PRESETS, resolved_preset
from config import build_config
from behavior import PROFILES, MOODS
from providers import test_openrouter, test_vibi, tiktok_live_check
from runtime import HostRuntime

app=Flask(__name__)
app.config['MAX_CONTENT_LENGTH']=64*1024
HOST=HostRuntime()
SHUTDOWN=threading.Event()
def memory_janitor():
    while not SHUTDOWN.wait(60): HOST.memory.cleanup_expired()
threading.Thread(target=memory_janitor,daemon=True,name="memory-cleanup").start()
PREVIEW_LOCK=threading.Semaphore(1)
RATE=defaultdict(deque)
RATE_LOCK=threading.Lock()


def clean_username(value):
    value=str(value or '').strip()
    match=re.search(r'tiktok\.com/@([^/?#]+)',value,re.I)
    value=(match.group(1) if match else value).lstrip('@').strip('/')
    if not re.fullmatch(r'[A-Za-z0-9._-]{2,64}',value):
        raise ValueError('رابط أو يوزر TikTok غير صالح')
    return value


def body():
    data=request.get_json(silent=True)
    if data is None: data={}
    if not isinstance(data,dict): raise ValueError('يجب إرسال JSON object')
    for k in ('persona','behavior'):
        if k in data and not isinstance(data[k],dict): raise ValueError('إعدادات الشخصية والسلوك يجب أن تكون object')
    return data


@app.before_request
def protect_control_api():
    if not request.path.startswith('/api/'):
        return None
    expected=os.getenv('CONTROL_TOKEN','')
    if not expected: return jsonify(ok=False,error='CONTROL_TOKEN غير مضبوط على السيرفر'),503
    received=request.headers.get('X-Control-Token','')
    if not hmac.compare_digest(received.encode(),expected.encode()):
        return jsonify(ok=False,error='غير مصرح'),401
    if request.method=='POST' and request.path!='/api/player/heartbeat':
        actor=request.headers.get('X-Operator-Id',request.remote_addr or 'local')[:80]
        with RATE_LOCK:
            now=time.time()
            # Bound the limiter itself.
            if len(RATE)>1000:
                for k in list(RATE):
                    if not RATE[k] or now-RATE[k][-1]>60: RATE.pop(k,None)
            q=RATE[actor]
            while q and now-q[0]>60: q.popleft()
            if len(q)>=120: return jsonify(ok=False,error='طلبات كثيرة، انتظر قليلًا'),429
            q.append(now)


@app.errorhandler(Exception)
def safe_error(exc):
    if isinstance(exc,HTTPException): return jsonify(ok=False,error=exc.name),exc.code
    if isinstance(exc,(ValueError,TypeError)):
        message=str(exc)
        return jsonify(ok=False,error=message[:160] if re.match(r'^[\u0600-\u06ff]',message) else 'بيانات غير صالحة'),400
    return jsonify(ok=False,error='تعذر إكمال الطلب؛ افحص حالة الخدمات'),502


@app.get('/')
def index(): return jsonify(ok=True,name='TikTok AI LIVE backend',version='2.0.0',control='Use the Vercel control panel')


@app.get('/health')
def health(): return jsonify(ok=True,service='tiktok-ai-live-cyber',version='2.0.0')


@app.get('/api/status')
def api_status(): return jsonify(HOST.status())


@app.get('/api/health/details')
def api_health(): return jsonify(HOST.health())


@app.get('/api/events')
def api_events():
    try: since=max(0,int(request.args.get('since','0')))
    except ValueError: since=0
    with HOST.lock:
        # Metadata only. LIVE audio is available only to the player endpoint.
        rows=[x for x in HOST.events if x['id']>since][:100]
        return jsonify(events=rows,latest_id=HOST.event_seq,boot_id=HOST.boot_id)


@app.get('/api/audio/events')
def api_audio_events():
    since=int(request.args.get('since','0'))
    with HOST.lock:
        active=request.args.get('player_id')==HOST.player['id'] and time.time()-HOST.player['heartbeat']<10
        rows=[x for x in HOST.audio if x['id']>since and time.time()-x['created_at']<60 and HOST.pending_audio and x['id']==HOST.pending_audio[0]] if active and HOST.running and not HOST.muted else []
        if request.args.get('initialize')=='1': rows=[]
        return jsonify(events=rows[:1],latest_id=HOST.event_seq,boot_id=HOST.boot_id,epoch=HOST.audio_epoch,active=active)


@app.get('/api/audio/chunks')
def api_audio_chunks():
    return jsonify(HOST.audio_chunks(request.args.get('audio_id','0'),request.args.get('since','0'),request.args.get('player_id','')))


@app.post('/api/player/heartbeat')
def api_heartbeat(): return jsonify(HOST.heartbeat(body()))


@app.get('/api/presets')
def api_presets():
    rows=[]
    for k in CHARACTER_PRESETS:
        p=dict(resolved_preset(k),tts_model='elevenlabs/eleven-v4-turbo',voice_language='ar',
            mood_default=CHARACTER_PRESETS[k]['mood_default'])
        if p.get('voice_gender')=='أنثى':
            p['voice']='gVzwmdZzRgBrNjXaTmi5';p['voice_label']='Layan — Saudi'
        else:
            p['voice']='yXEnnEln9armDCyhkXcA';p['voice_label']='Jeddawi — Saudi Male'
        rows.append(p)
    return jsonify(ok=True,presets=rows)


@app.get('/api/profiles')
def api_profiles(): return jsonify(ok=True,profiles=[dict(id=k,**v) for k,v in PROFILES.items()],moods=MOODS)


@app.get('/api/metrics')
def api_metrics(): return jsonify(ok=True,metrics=HOST.self_host_metrics())


@app.get('/api/self-host/status')
def api_self_host_status(): return jsonify(HOST.self_host_status())


@app.post('/api/self-host/configure')
def api_self_host_configure(): return jsonify(HOST.self_host_configure(body()))


@app.post('/api/self-host/new-topic')
def api_self_host_new_topic(): return jsonify(HOST.self_host_action('new-topic'))


@app.post('/api/self-host/stop-topic')
def api_self_host_stop_topic(): return jsonify(HOST.self_host_action('stop-topic'))


@app.post('/api/self-host/speak-now')
def api_self_host_speak_now(): return jsonify(HOST.self_host_action('speak-now'))


@app.post('/api/self-host/pause')
def api_self_host_pause(): return jsonify(HOST.self_host_action('pause'))


@app.post('/api/self-host/resume')
def api_self_host_resume(): return jsonify(HOST.self_host_action('resume'))


@app.post('/api/metrics/reset')
def api_reset():
    HOST.metrics.reset()
    return jsonify(ok=True,metrics=HOST.metrics.snapshot())


@app.post('/api/configure')
def api_configure():
    j=HOST.configure(body())
    return jsonify({**j,'character':j['agent_name'],'voice':j['tts_voice']})


@app.post('/api/start')
def api_start():
    data=body();data['tiktok']=clean_username(data.get('tiktok'))
    return jsonify({**HOST.start(data),'message':'بدأ الاتصال'})


@app.post('/api/stop')
def api_stop(): return jsonify(HOST.stop())


@app.post('/api/restart')
def api_restart():
    data=body();data['tiktok']=clean_username(data.get('tiktok') or HOST.username)
    with HOST.lifecycle:
        HOST.stop()
        return jsonify(HOST.start(data))


@app.post('/api/control')
def api_control():
    data=body();action=data.get('action')
    with HOST.lock:
        if action in ('pause','resume'): HOST.paused=action=='pause'
        elif action=='mute':
            HOST.muted=bool(data.get('muted',True)); HOST.audio_epoch+=1; HOST.audio.clear();HOST.pending_audio=None
        elif action=='volume': HOST.volume=max(0,min(1,float(data.get('volume',1))))
        elif action=='clear_queue':
            for event in HOST.queue.clear(): HOST.skip(event,'queue cleared')
            HOST.audio.clear();HOST.audio_epoch+=1;HOST.pending_audio=None
        elif action=='speak': HOST.ingest(dict(type='manual',text=str(data.get('text',''))[:300],username='operator',name='المضيف'))
        else: raise ValueError('إجراء غير مدعوم')
    return jsonify(HOST.status())


@app.post('/api/simulation/start')
def api_simulation_start():
    data=body()
    return jsonify(HOST.start(data,simulation=True,fixture=data.get('fixture') is True))


@app.post('/api/simulate')
def api_simulate():
    data=body()
    with HOST.lock:
        if not HOST.running or not HOST.simulation: raise ValueError('ابدأ وضع المحاكاة أولًا؛ البث الحقيقي لا يقبل أحداث تجريبية')
        sim_type=data.get('type')
        if sim_type=='idle':
            HOST.self_host.simulate_idle(max(1,min(300,float(data.get('seconds',30)))))
            return jsonify(ok=True,simulation='idle',self_host=HOST.self_host_status())
        if sim_type=='topic_response':
            row=HOST.ingest(dict(type='comment',username=data.get('username','sim_viewer'),text=data.get('text','سنايبر')))
            return jsonify(ok=True,event_id=row['event_id'],linked=bool(row.get('self_host_topic_reply')))
        if sim_type=='no_response':
            HOST.self_host.simulate_idle(60)
            HOST.self_host.speak_now()
            return jsonify(ok=True,simulation='no_response')
        if sim_type=='owner_talking':
            return jsonify(HOST.self_host_action('owner-talking',{'value':bool(data.get('value',True))}))
        if sim_type=='burst':
            count=max(1,min(80,int(data.get('count',30))))
            rows=[]
            for i in range(count):
                text=['هلا','وش تلعب؟','سايبر كيف أضبط السنايبر؟','كفو','هههه رهيب'][i%5]
                rows.append(HOST.ingest(dict(type='comment',username=f'burst_{i}',text=text))['event_id'])
            return jsonify(ok=True,event_ids=rows)
        row=HOST.ingest(data)
        return jsonify(ok=True,event_id=row['event_id'],classification=row['classification'],priority=row['priority_score'])


@app.post('/api/preview')
def api_preview():
    data=body()
    if not PREVIEW_LOCK.acquire(blocking=False): return jsonify(ok=False,error='معاينة أخرى قيد التشغيل'),429
    try:
        with HOST.lock: cfg=build_config({**HOST.cfg,**data})
        if not cfg['openrouter_key']: raise ValueError('مفتاح OpenRouter مطلوب')
        event=dict(type=data.get('preview_type','comment'),name='مشاهد تجريبي',text=str(data.get('preview_text','السلام عليكم'))[:300])
        event['classification']=HOST.classifier.classify(event['text'])
        cfg['voice_style']=HOST.voice.build_tts_style(event,HOST.mood.current,cfg)
        reply=HOST.providers.reply(event,cfg)
        audio_b64,mime=HOST.providers.speak(reply,cfg)
        return jsonify(ok=True,reply=reply,audio_b64=audio_b64,mime=mime)
    finally: PREVIEW_LOCK.release()


@app.post('/api/test')
def api_test():
    data=body();username=clean_username(data.get('tiktok'))
    with HOST.lock: cfg=build_config({**HOST.cfg,**data})
    result=dict(ok=True,username=username)
    try:
        ok,msg=test_openrouter(cfg['openrouter_key']);result['openrouter']=dict(ok=ok,message=msg)
    except Exception: result['openrouter']=dict(ok=False,message='تعذر فحص OpenRouter')
    try:
        if not PREVIEW_LOCK.acquire(blocking=False): raise RuntimeError()
        try: HOST.providers.speak('يا هلا، هذا اختبار الصوت.',cfg)
        finally: PREVIEW_LOCK.release()
        result['tts']=dict(ok=True,message='تم توليد صوت تجريبي بنجاح',model=cfg['tts_model'])
    except Exception: result['tts']=dict(ok=False,message='فشل اختبار توليد الصوت')
    result['vibi']=dict(ok=True,message='احتياطي عند ضبط مفتاح وصوت مناسبين')
    try:
        live=tiktok_live_check(username);result['tiktok']=dict(ok=bool(live),message='LIVE' if live else 'الحساب غير متصل الآن')
    except Exception: result['tiktok']=dict(ok=False,message='تعذر فحص TikTok')
    result['ok']=all(result[k]['ok'] for k in ('openrouter','tts','tiktok'))
    return jsonify(result)


atexit.register(SHUTDOWN.set)
atexit.register(HOST.stop)
if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')),threaded=True)

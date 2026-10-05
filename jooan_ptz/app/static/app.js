'use strict';

let lastData=null;
let authenticated=false;
let activeDirection=null;
let statusTimer=null;
let ptzSequence=0;
let ptzHoldGeneration=0;
let manualSnapshotBusy=false;
let ptzSnapshotRequests=0;
const snapshotUrls={0:null,1:null};
const STATUS_REFRESH_MS=5000;
const PTZ_SNAPSHOT_INTERVAL_MS=1000;

const ingressBase=new URL(window.location.href);
ingressBase.search='';
ingressBase.hash='';
if(!ingressBase.pathname.endsWith('/'))ingressBase.pathname+='/';

const ptzClient=(globalThis.crypto?.randomUUID?.()||('ptz-'+Math.random().toString(36).slice(2)));

const $=id=>document.getElementById(id);
const api=path=>new URL(path,ingressBase).toString();
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const esc=value=>String(value??'')
  .replaceAll('&','&amp;')
  .replaceAll('<','&lt;')
  .replaceAll('>','&gt;')
  .replaceAll('"','&quot;')
  .replaceAll("'","&#039;");
const boolState=value=>Number(value)===1||value===true;
const videoInfo=item=>(item?.streams||[]).find(stream=>stream?.codec_type==='video')||{};

function formatTime(value){
  if(!value)return '—';
  try{return new Date(Number(value)*1000).toLocaleString('pt-BR')}catch(_){return '—'}
}

function formatMb(value){
  const number=Number(value);
  if(!Number.isFinite(number))return '—';
  if(number>=1000)return (number/1000).toFixed(number>=10000?1:2)+' GB';
  return number.toFixed(0)+' MB';
}

function settingValue(value){
  if(value===null||value===undefined)return '—';
  if(typeof value==='boolean')return value?'true':'false';
  if(typeof value==='object'){
    try{return JSON.stringify(value)}catch(_){return String(value)}
  }
  return String(value);
}

function ptzUrl(command,sequence){
  const url=new URL(api('api/ptz/'+command));
  url.searchParams.set('client',ptzClient);
  url.searchParams.set('seq',String(sequence));
  if(command!=='stop'){
    url.searchParams.set('speed',String($('ptzSpeed')?.value||'0.4'));
    url.searchParams.set('duration_ms','280');
  }
  return url.toString();
}

function statusBadge(enabled,onLabel='Ativo',offLabel='Inativo'){
  return '<span class="badge '+(enabled?'success':'neutral')+'"><span class="state-dot '+(enabled?'state-on':'state-off')+'"></span>'+
    esc(enabled?onLabel:offLabel)+'</span>';
}

function setHealth(data){
  const badge=$('headerStatus');
  const banner=$('statusBanner');
  const ready=!!data.online&&!!data.authenticated;
  const authPending=!!data.online&&!data.authenticated;

  badge.className='health-badge '+(ready?'health-online':authPending?'health-warning':'health-offline');
  badge.innerHTML='<span class="health-dot"></span><span>'+
    (ready?'Online':authPending?'Online · autenticação pendente':'Offline')+'</span>';

  if(!data.online){
    banner.className='status-banner bad';
    banner.textContent='Offline · mantendo os últimos dados conhecidos e bloqueando novas capturas.';
  }else if(!data.authenticated){
    banner.className='status-banner warning';
    banner.textContent='LAN acessível · aguardando validação CGI.';
  }else if(data.service_degraded){
    banner.className='status-banner warning';
    banner.textContent='Autenticação preservada · serviço da câmera respondeu de forma instável na última validação.';
  }else if(data.validation_running){
    banner.className='status-banner warning';
    banner.textContent='Validação em andamento · mídia e PTZ temporariamente protegidos.';
  }else if(activeDirection){
    banner.className='status-banner warning';
    banner.textContent='PTZ em movimento · atualizando a lente PTZ por snapshot em até 1 FPS.';
  }else if(ptzSnapshotRequests>0){
    banner.className='status-banner warning';
    banner.textContent='Atualizando posição do PTZ · nenhuma sessão de vídeo contínua aberta.';
  }else if(manualSnapshotBusy){
    banner.className='status-banner warning';
    banner.textContent='Capturando snapshot · outras ações de mídia aguardam a conclusão.';
  }else{
    banner.className='status-banner ok';
    banner.textContent='Autenticada · PTZ pronto · RTSP usado somente para snapshots sob demanda.';
  }

  const device=data.device_info||{};
  const model=device.model||data.lan_support?.model||'Câmera local';
  $('headerTitle').textContent=model;
  const meta=[
    device.firmware_version?['FW',device.firmware_version]:null,
    device.timezone?['Fuso',device.timezone]:null,
    data.authenticated?['Auth','OK']:null,
    ['RTSP','snapshots'],
    ['PTZ','preview 1 FPS']
  ].filter(Boolean);
  $('headerSubtitle').innerHTML=meta.map(([label,value])=>
    '<span class="header-meta-item"><span>'+esc(label)+'</span> '+esc(value)+'</span>'
  ).join('');
}

function renderOverview(data){
  const device=data.device_info||{};
  const state=device.local_state||{};
  const services=data.services||{};
  const total=device.sdcard_total_mb;
  const networkRaw=data.network_state?.NETWORKSTATE;
  const networkFriendly=networkRaw==='CONNECTBUTT'?'Conectada':(networkRaw||'Sem estado reportado');
  const rtspConfigured=!!data.stream_info?.credentials_confirmed;

  $('overviewCards').innerHTML=[
    {
      label:'Dispositivo',
      value:device.model||data.lan_support?.model||'—',
      sub:(device.channel_count||'—')+' canais · '+(device.capabilities?.codec||'codec desconhecido')
    },
    {
      label:'Rede',
      value:data.online?'Online':'Offline',
      sub:networkFriendly
    },
    {
      label:'Armazenamento',
      value:formatMb(total),
      sub:device.sdcard_free_mb===0?'0 MB livre reportado':formatMb(device.sdcard_free_mb)+' livre'
    },
    {
      label:'Mídia',
      value:rtspConfigured?'RTSP configurado':'RTSP não confirmado',
      sub:'porta '+(services.rtsp?.port||554)+' · snapshots sob demanda'
    }
  ].map(item=>
    '<div class="stat-card"><span class="label">'+esc(item.label)+'</span><div><div class="value">'+
    esc(item.value)+'</div><div class="subvalue">'+esc(item.sub)+'</div></div></div>'
  ).join('');

  const quick=[
    ['Movimento','motion_detection','md_enable'],
    ['Gravação','recording','record_enable'],
    ['LED','led','led'],
    ['Holofote','floodlight','floodlight'],
    ['Auto tracking','automatic_tracking','autotrack'],
    ['Pessoa','person_detection','person_detect'],
    ['Veículo','vehicle_detection','vehicle_detect'],
    ['Rastreamento pessoa','person_tracking','person_track_enable']
  ];
  $('quickFeatures').innerHTML=quick.map(([label,cap,key])=>{
    const supported=device.capabilities?.[cap]===true;
    const enabled=boolState(state[key]);
    return '<div class="feature-item"><div class="feature-label"><strong>'+esc(label)+'</strong><span>'+
      (supported?'Suportado pela câmera':'Não detectado')+'</span></div>'+statusBadge(enabled)+'</div>';
  }).join('');

  const serviceDefs=[
    ['HTTP',services.http,'80','CGI autenticado'],
    ['API local',services.features,'9898','Estado OEM'],
    ['RTSP',services.rtsp,'554','Snapshots sob demanda'],
    ['ONVIF',services.onvif,'8899','Usado pelo PTZ quando disponível']
  ];
  $('serviceList').innerHTML=serviceDefs.map(([label,item,fallback,desc])=>{
    let className='neutral';
    let text='Não testado';
    if(item?.reachable===true){className='success';text='Disponível'}
    else if(item?.reachable===false){className='danger';text='Indisponível'}
    else if(label==='RTSP'&&item?.configured){className='warning';text='Configurado'}
    return '<div class="service-row"><div class="service-left"><div class="service-icon">'+esc(label.slice(0,2))+
      '</div><div><strong>'+esc(label)+'</strong><small>'+esc(desc)+' · porta '+esc(item?.port||fallback)+
      '</small></div></div><span class="badge '+className+'">'+esc(text)+'</span></div>';
  }).join('');
}

function renderDetection(data){
  const device=data.device_info||{};
  const state=device.local_state||{};
  const capabilities=device.capabilities||{};
  const defs=[
    ['Detecção de movimento','motion_detection','md_enable',state.mdsensitivity!=null?'Sensibilidade '+state.mdsensitivity:null],
    ['Movimento secundário','secondary_motion_detection','sub_md_enable',state.sub_mdsensitivity!=null?'Sensibilidade '+state.sub_mdsensitivity:null],
    ['Detecção de pessoa','person_detection','person_detect',null],
    ['Detecção de veículo','vehicle_detection','vehicle_detect',null],
    ['Rastreamento automático','automatic_tracking','autotrack',null],
    ['Rastreamento de pessoa','person_tracking','person_track_enable',null]
  ];
  $('detectionGrid').innerHTML=defs.map(([label,cap,key,sub])=>{
    const supported=capabilities[cap]===true;
    const exists=Object.prototype.hasOwnProperty.call(state,key);
    const enabled=boolState(state[key]);
    const value=exists?(enabled?'Ativo':'Inativo'):'—';
    return '<div class="setting-item"><div><strong>'+esc(label)+'</strong><span>'+
      esc(sub||(supported?'Suportado':'Não detectado'))+'</span></div><span class="setting-value">'+esc(value)+'</span></div>';
  }).join('');

  const lights=[
    ['LED','led','led'],
    ['Holofote','floodlight','floodlight'],
    ['Luz amarela','yellow_light','yellowlight'],
    ['Buzzer',null,'buzzer'],
    ['Push',null,'msgpush_enable'],
    ['Sensibilidade de áudio',null,'audiosensitive']
  ];
  $('lightingGrid').innerHTML=lights.map(([label,cap,key])=>{
    const exists=Object.prototype.hasOwnProperty.call(state,key);
    const supported=cap?capabilities[cap]===true:exists;
    const raw=state[key];
    const value=exists?(boolState(raw)?'Ativo':String(raw??'Inativo')):'—';
    return '<div class="setting-item"><div><strong>'+esc(label)+'</strong><span>'+
      (supported?'Estado reportado':'Não detectado')+'</span></div><span class="setting-value">'+esc(value)+'</span></div>';
  }).join('');
}

function scheduleSummary(schedule){
  if(!schedule||typeof schedule!=='object')return 'Não reportada';
  const full='00:00:00-24:00:00';
  const weekArrays=[1,2,3,4,5,6,7].map(day=>schedule['week'+day]).filter(Array.isArray);
  if(weekArrays.length===7&&weekArrays.every(list=>list.length===1&&list[0]===full))return '24 horas · 7 dias';
  if(schedule.start_time==='00:00:00'&&schedule.stop_time==='24:00:00')return '24 horas';
  if(schedule.start_time||schedule.stop_time)return (schedule.start_time||'—')+' → '+(schedule.stop_time||'—');
  return 'Agenda personalizada';
}

function renderRecording(data){
  const device=data.device_info||{};
  const state=device.local_state||{};
  const props=device.properties||{};
  const total=Number(device.sdcard_total_mb||0);
  const free=Number(device.sdcard_free_mb);
  let percent=null;
  if(total>0&&free>=0&&free<=total)percent=Math.max(0,Math.min(100,((total-free)/total)*100));

  $('storageCard').innerHTML=
    '<div class="storage-hero"><div><span class="helper">Capacidade reportada</span><div class="big">'+esc(formatMb(total))+
    '</div></div><span class="badge '+(device.capabilities?.sdcard?'success':'neutral')+'">'+
    (device.capabilities?.sdcard?'SD suportado':'Sem capability')+'</span></div>'+
    '<div class="progress"><span style="width:'+(percent==null?0:percent)+'%"></span></div>'+
    '<div class="info-list"><div class="info-row"><span>Livre reportado</span><strong>'+esc(formatMb(free))+
    '</strong></div><div class="info-row"><span>Status bruto</span><strong>'+esc(state.sdcard_status??'—')+
    '</strong></div><div class="info-row"><span>Exceção bruta</span><strong>'+esc(state.sdcard_excepreason??'—')+
    '</strong></div></div>';

  $('recordingCard').innerHTML=
    '<div class="storage-hero"><div><span class="helper">Gravação local</span><div class="big">'+
    (boolState(state.record_enable)?'Ligada':'Desligada')+'</div></div>'+statusBadge(boolState(state.record_enable))+'</div>'+
    '<div class="info-list"><div class="info-row"><span>Tipo bruto</span><strong>'+esc(state.record_type??props.rectype??'—')+
    '</strong></div><div class="info-row"><span>Canal reportado</span><strong>'+esc(state.recordechannel??'—')+
    '</strong></div><div class="info-row"><span>Agenda principal</span><strong>'+
    esc(scheduleSummary(props.newrecord_schedule||props.record_schedule))+'</strong></div></div>';

  const schedules=[
    ['Gravação',props.newrecord_schedule||props.record_schedule],
    ['Push',props.newmsg_push_schedule||props.msgpush_schedule],
    ['Ocultação PTZ',props.ptz_hide_schedule],
    ['Alarme sonoro',props.sound_alarm_schedule],
    ['Luz de alarme',props.alarm_light_schedule],
    ['Holofote',props.newflood_light_schedule||props.light_schedule]
  ];
  $('scheduleGrid').innerHTML=schedules.map(([label,schedule])=>
    '<div class="schedule-item"><strong>'+esc(label)+'</strong><span>'+esc(scheduleSummary(schedule))+'</span></div>'
  ).join('');
}

function renderFeedMeta(data,channel){
  const channelId='ch'+String(channel).padStart(2,'0');
  const path='/live/'+channelId+'_0';
  const isPtz=(Number(data?.ptz_channel)===channel)||(data?.ptz_channel==null&&channel===0);
  const meta=$('feedMeta'+channel);
  meta.innerHTML=[
    'Snapshot RTSP',
    path,
    isPtz?'Preview PTZ · máx. 1 FPS':null
  ].filter(Boolean).map(value=>'<span class="meta-pill">'+esc(value)+'</span>').join('');
}

function ptzPreviewChannel(data=lastData){
  return Number(data?.ptz_channel)===1?1:0;
}

function renderGeneralInfo(data){
  const device=data.device_info||{};
  const props=device.properties||{};
  const services=data.services||{};
  const rows=[
    ['Modelo',device.model||data.lan_support?.model||'—'],
    ['Firmware',device.firmware_version||props.device_version||'—'],
    ['ID',device.device_id||data.lan_support?.device_id||'—'],
    ['MAC',device.mac||props.device_mac||'—'],
    ['IP reportado',props.device_ip||data.network_state?.IP||'—'],
    ['Canais',device.channel_count??data.stream_info?.reported_channel_count??'—'],
    ['Codec',device.capabilities?.codec||props.solution||'—'],
    ['Timezone',device.timezone||props.timezone||'—'],
    ['RTSP',data.stream_info?.credentials_confirmed?'Credenciais obtidas · porta '+(services.rtsp?.port||554):'Não confirmado'],
    ['ONVIF',services.onvif?.reachable===true?'Disponível':services.onvif?.reachable===false?'Indisponível':'Não testado'],
    ['PTZ',data.ptz_onvif_available?'ONVIF + fallback CGI':'CGI'],
    ['Último contato',formatTime(data.last_seen)]
  ];
  $('generalInfo').innerHTML=rows.map(([label,value])=>
    '<div class="info-row"><span>'+esc(label)+'</span><strong>'+esc(settingValue(value))+'</strong></div>'
  ).join('');
}

function renderSettingGroup(title,value){
  const entries=Object.entries(value||{}).sort(([a],[b])=>a.localeCompare(b));
  if(!entries.length)return '';
  return '<section class="read-settings-group"><h3>'+esc(title)+'</h3><div class="read-setting-grid">'+
    entries.map(([key,item])=>
      '<div class="read-setting"><span>'+esc(key)+'</span><strong>'+esc(settingValue(item))+'</strong></div>'
    ).join('')+'</div></section>';
}

function renderAllReadSettings(data){
  const device=data.device_info||{};
  const onvif=data.onvif_info||{};
  const ptz=onvif.ptz||{};
  const network={
    ...(data.network_state||{}),
    lan_support:data.lan_support||null
  };
  const media={
    rtsp_port:data.services?.rtsp?.port||554,
    credentials_confirmed:data.stream_info?.credentials_confirmed??null,
    candidate_paths:data.stream_info?.candidate_paths||[],
    ptz_snapshot_interval_ms:PTZ_SNAPSHOT_INTERVAL_MS,
    ptz_snapshot_channel:ptzPreviewChannel(data)
  };
  const onvifState={
    reachable:onvif.reachable??null,
    profiles:onvif.profiles||[],
    ptz_profile_token:ptz.profile_token||null,
    ptz_nodes:ptz.nodes||[],
    ptz_status:ptz.status||null,
    services:onvif.services||{}
  };

  $('allReadSettings').innerHTML=[
    renderSettingGroup('Estado OEM',device.local_state||{}),
    renderSettingGroup('Propriedades OEM',device.properties||{}),
    renderSettingGroup('Device Features',device.device_features||{}),
    renderSettingGroup('Capabilities interpretadas',device.capabilities||{}),
    renderSettingGroup('Plataforma',data.camera_info||{}),
    renderSettingGroup('Rede e LAN',network),
    renderSettingGroup('RTSP',media),
    renderSettingGroup('ONVIF',onvifState)
  ].filter(Boolean).join('')||'<div class="empty">Nenhuma leitura disponível.</div>';
}

function renderControl(data){
  renderFeedMeta(data,0);
  renderFeedMeta(data,1);
  renderGeneralInfo(data);

  const frameChannel=ptzPreviewChannel(data);
  for(const channel of [0,1]){
    const isPtz=channel===frameChannel;
    const tile=$('feedTile'+channel);
    const title=$('feedTitle'+channel);
    const marker=$('feedPtzMarker'+channel);
    if(tile)tile.classList.toggle('feed-tile-ptz',isPtz);
    if(title)title.textContent='Lente '+(channel+1)+' · ch'+String(channel).padStart(2,'0')+(isPtz?' · PTZ':'');
    if(marker)marker.hidden=!isPtz;
  }

  $('ptzState').textContent=data.ptz_moving?'Em movimento':'Parado';
  $('ptzState').className='badge '+(data.ptz_moving?'warning':'neutral');


  const frameBadge=$('ptzFrameBadge');
  if(frameBadge){
    frameBadge.textContent=activeDirection
      ?'Capturando · Lente '+(frameChannel+1)+' · 1 FPS'
      :'Preview PTZ · Lente '+(frameChannel+1)+' · 1 FPS';
    frameBadge.className='badge '+(activeDirection?'warning':'neutral');
  }

  const transport=data.ptz_last_transport;
  const badge=$('ptzTransportBadge');
  if(transport==='onvif'){
    badge.textContent='ONVIF · velocidade ativa';
    badge.className='badge success';
  }else if(transport==='cgi-fallback'){
    badge.textContent='CGI · fallback';
    badge.className='badge warning';
  }else if(data.ptz_onvif_available){
    badge.textContent='ONVIF preferido';
    badge.className='badge success';
  }else{
    badge.textContent='CGI PTZ';
    badge.className='badge neutral';
  }
}

function enableControls(data){
  const ready=!!data.online&&!!data.authenticated&&!data.validation_running;
  const ptzEnabled=ready&&!manualSnapshotBusy;
  document.querySelectorAll('[data-dir]').forEach(button=>button.disabled=!ptzEnabled);
  $('stop').disabled=!ptzEnabled;
  $('ptzSpeed').disabled=!ptzEnabled;

  const mediaBusy=manualSnapshotBusy||!!activeDirection||ptzSnapshotRequests>0;
  $('refreshSnapshots').disabled=!ready||mediaBusy;
  document.querySelectorAll('[data-snapshot-channel]').forEach(button=>{
    button.disabled=!ready||mediaBusy;
  });
}

function renderAll(data){
  setHealth(data);
  renderOverview(data);
  renderControl(data);
  renderDetection(data);
  renderRecording(data);
  renderAllReadSettings(data);
  enableControls(data);

  if(data.last_error&&!data.validation_running){
    $('command').textContent='Último erro: '+data.last_error;
  }
}

async function refreshStatus(){
  try{
    const response=await fetch(api('api/status'),{cache:'no-store'});
    if(!response.ok)throw new Error('HTTP '+response.status);
    const data=await response.json();
    lastData=data;
    authenticated=!!data.authenticated;
    renderAll(data);
  }catch(error){
    authenticated=false;
    $('headerStatus').className='health-badge health-offline';
    $('headerStatus').innerHTML='<span class="health-dot"></span><span>App indisponível</span>';
    $('statusBanner').className='status-banner bad';
    $('statusBanner').textContent='App/API indisponível: '+error.message;
  }
}

function setFeedUi(channel,state,message){
  const snapshot=$('snapshotFeed'+channel);
  const empty=$('feedEmpty'+channel);
  const badge=$('feedBadge'+channel);
  const text=$('feedMessage'+channel);

  if(state==='loading'){
    empty.hidden=!!snapshotUrls[channel];
    snapshot.hidden=!snapshotUrls[channel];
    badge.textContent='Capturando';
    badge.className='badge warning';
  }else if(state==='snapshot'){
    snapshot.hidden=false;
    empty.hidden=true;
    badge.textContent='Snapshot';
    badge.className='badge neutral';
  }else if(state==='error'){
    snapshot.hidden=!snapshotUrls[channel];
    empty.hidden=!!snapshotUrls[channel];
    badge.textContent='Falha';
    badge.className='badge danger';
  }else{
    snapshot.hidden=!snapshotUrls[channel];
    empty.hidden=!!snapshotUrls[channel];
    badge.textContent=snapshotUrls[channel]?'Snapshot':'Parado';
    badge.className='badge neutral';
  }

  if(message)text.textContent=message;
  if(lastData)enableControls(lastData);
}

async function loadSnapshotChannel(channel,{ptzPreview=false,finalFrame=false,allowManualBusy=false}={}){
  if(
    !ptzPreview
    && (
      (manualSnapshotBusy&&!allowManualBusy)
      || activeDirection
      || ptzSnapshotRequests>0
    )
  )return false;

  const ownsManualBusy=!ptzPreview&&!allowManualBusy;
  if(ownsManualBusy)manualSnapshotBusy=true;
  if(ptzPreview)ptzSnapshotRequests++;

  if(lastData){
    setHealth(lastData);
    enableControls(lastData);
  }

  const stream='ch'+String(channel).padStart(2,'0')+'_0';
  if(ptzPreview){
    const badge=$('feedBadge'+channel);
    badge.textContent='PTZ · capturando';
    badge.className='badge warning';
    $('feedMessage'+channel).textContent=finalFrame
      ?'Capturando posição final do PTZ...'
      :'Atualizando durante movimento PTZ · máximo 1 frame/s.';
  }else{
    setFeedUi(channel,'loading','Capturando um frame do stream principal...');
  }

  try{
    const query='?t='+Date.now()+(ptzPreview?'&ptz=1':'');
    const response=await fetch(api('api/snapshot/'+stream+query),{cache:'no-store'});
    if(!response.ok)throw new Error('HTTP '+response.status);
    const blob=await response.blob();
    const objectUrl=URL.createObjectURL(blob);
    const old=snapshotUrls[channel];
    snapshotUrls[channel]=objectUrl;
    const image=$('snapshotFeed'+channel);
    image.src=objectUrl;
    if(old)URL.revokeObjectURL(old);

    const stale=response.headers.get('X-JOOAN-Snapshot')==='stale';
    let message;
    if(ptzPreview){
      message=stale
        ?'PTZ · câmera ocupada; exibindo último frame disponível.'
        :(finalFrame?'PTZ · posição final atualizada.':'PTZ · frame atualizado.');
    }else{
      message=stale?'Snapshot em cache temporário.':'Snapshot atualizado.';
    }
    setFeedUi(channel,'snapshot',message);
    return !stale;
  }catch(error){
    setFeedUi(channel,'error',(ptzPreview?'PTZ · ':'')+'Falha no snapshot: '+error.message);
    return false;
  }finally{
    if(ownsManualBusy)manualSnapshotBusy=false;
    if(ptzPreview)ptzSnapshotRequests=Math.max(0,ptzSnapshotRequests-1);
    if(lastData){
      setHealth(lastData);
      enableControls(lastData);
    }
  }
}

async function refreshSnapshots(){
  if(
    !lastData?.online||
    !lastData?.authenticated||
    manualSnapshotBusy||
    activeDirection||
    ptzSnapshotRequests>0
  )return;

  manualSnapshotBusy=true;
  if(lastData){
    setHealth(lastData);
    enableControls(lastData);
  }
  $('previewMessage').textContent='Atualizando as duas imagens, uma de cada vez...';

  try{
    await loadSnapshotChannel(0,{allowManualBusy:true});
    await sleep(350);
    await loadSnapshotChannel(1,{allowManualBusy:true});
    $('previewMessage').textContent='Snapshots concluídos.';
  }finally{
    manualSnapshotBusy=false;
    if(lastData){
      setHealth(lastData);
      enableControls(lastData);
    }
  }
}

async function ptzSnapshotLoop(direction,generation){
  const channel=ptzPreviewChannel();
  while(
    activeDirection===direction
    && generation===ptzHoldGeneration
    && !document.hidden
  ){
    const started=Date.now();
    await loadSnapshotChannel(channel,{ptzPreview:true});
    const elapsed=Date.now()-started;
    const wait=Math.max(0,PTZ_SNAPSHOT_INTERVAL_MS-elapsed);
    if(wait)await sleep(wait);
  }
}

async function stopPtzAndCaptureFinal(){
  const generation=ptzHoldGeneration;
  await sendPtz('stop');
  const channel=ptzPreviewChannel();
  const delays=[220,900,900];
  for(let index=0;index<delays.length;index++){
    await sleep(delays[index]);
    if(
      generation!==ptzHoldGeneration
      || activeDirection
      || document.hidden
      || !lastData?.online
      || !lastData?.authenticated
    )break;
    await loadSnapshotChannel(channel,{ptzPreview:true,finalFrame:true});
  }
  if(lastData)renderControl(lastData);
}

async function sendPtz(command,keepalive=false){
  if(!authenticated)return null;
  const sequence=++ptzSequence;
  try{
    const response=await fetch(ptzUrl(command,sequence),{method:'POST',keepalive});
    const body=await response.json();
    if(!body.ignored){
      const transport=body.transport?(' · '+body.transport):'';
      $('command').textContent=response.ok?'Comando: '+command+transport:'Erro: '+(body.error||'falha');
    }
    if(body.transport&&lastData){
      lastData.ptz_last_transport=body.transport;
      renderControl(lastData);
    }
    if(!response.ok&&response.status===401)authenticated=false;
    return response.ok?body:null;
  }catch(error){
    $('command').textContent='Erro: '+error.message;
    return null;
  }
}

async function holdPtz(direction,generation){
  let result=await sendPtz(direction);
  if(
    result
    && activeDirection===direction
    && generation===ptzHoldGeneration
  ){
    void ptzSnapshotLoop(direction,generation);
  }
  while(activeDirection===direction&&generation===ptzHoldGeneration&&result?.transport==='onvif'){
    await sleep(80);
    if(activeDirection!==direction||generation!==ptzHoldGeneration)break;
    result=await sendPtz(direction);
  }
}

function emergencyStop(){
  if(!activeDirection)return;
  activeDirection=null;
  ptzHoldGeneration++;
  if(lastData){
    setHealth(lastData);
    renderControl(lastData);
    enableControls(lastData);
  }
  void sendPtz('stop',true);
}

document.querySelectorAll('[data-snapshot-channel]').forEach(button=>{
  button.addEventListener('click',()=>loadSnapshotChannel(Number(button.dataset.snapshotChannel)));
});

document.querySelectorAll('[data-dir]').forEach(button=>{
  const direction=button.dataset.dir;
  button.addEventListener('pointerdown',event=>{
    event.preventDefault();
    activeDirection=direction;
    const generation=++ptzHoldGeneration;
    if(lastData){
      setHealth(lastData);
      renderControl(lastData);
      enableControls(lastData);
    }
    button.setPointerCapture?.(event.pointerId);
    void holdPtz(direction,generation);
  });
  const stop=()=>{
    if(activeDirection===direction){
      activeDirection=null;
      ptzHoldGeneration++;
      if(lastData){
        setHealth(lastData);
        renderControl(lastData);
        enableControls(lastData);
      }
      void stopPtzAndCaptureFinal();
    }
  };
  button.addEventListener('pointerup',stop);
  button.addEventListener('pointercancel',stop);
  button.addEventListener('lostpointercapture',stop);
});

$('stop').addEventListener('click',()=>{
  activeDirection=null;
  ptzHoldGeneration++;
  if(lastData){
    setHealth(lastData);
    renderControl(lastData);
    enableControls(lastData);
  }
  void stopPtzAndCaptureFinal();
});
$('refreshSnapshots').addEventListener('click',refreshSnapshots);

window.addEventListener('blur',emergencyStop);
document.addEventListener('visibilitychange',()=>{
  if(document.hidden)emergencyStop();
});
window.addEventListener('pagehide',()=>{
  emergencyStop();
  for(const url of Object.values(snapshotUrls))if(url)URL.revokeObjectURL(url);
});

void refreshStatus();
statusTimer=setInterval(()=>{if(!document.hidden)void refreshStatus()},STATUS_REFRESH_MS);

'use strict';

let lastData=null;
let online=false;
let authenticated=false;
let activeDirection=null;
let selectedChannel=0;
let liveActive=false;
let currentTab='overview';
let statusTimer=null;
let snapshotTimer=null;
let snapshotRefreshRunning=false;
let lastOverviewSignature='';
let lastCameraSignature='';
let labPresets=[];
let labOemRecordingBaseline=null;
let labOemToggleBaseline=null;
let labRequestRunning=false;
const STATUS_REFRESH_MS=5000;
const SNAPSHOT_REFRESH_MS=30000;

const ingressBase=new URL(window.location.href);
ingressBase.search='';
ingressBase.hash='';
if(!ingressBase.pathname.endsWith('/'))ingressBase.pathname+='/';

const ptzClient=(globalThis.crypto?.randomUUID?.()||('ptz-'+Math.random().toString(36).slice(2)));
let ptzSequence=0;

const $=id=>document.getElementById(id);
const esc=value=>String(value??'')
  .replaceAll('&','&amp;')
  .replaceAll('<','&lt;')
  .replaceAll('>','&gt;')
  .replaceAll('"','&quot;')
  .replaceAll("'","&#039;");
const api=path=>new URL(path,ingressBase).toString();
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const boolState=value=>Number(value)===1||value===true;
const formatTime=value=>{
  if(!value)return '—';
  try{return new Date(Number(value)*1000).toLocaleString('pt-BR')}catch(_){return '—'}
};
const formatMb=value=>{
  const number=Number(value);
  if(!Number.isFinite(number))return '—';
  if(number>=1000)return (number/1000).toFixed(number>=10000?1:2)+' GB';
  return number.toFixed(0)+' MB';
};
const videoInfo=item=>(item?.streams||[]).find(s=>s.codec_type==='video')||{};
const audioInfo=item=>(item?.streams||[]).find(s=>s.codec_type==='audio')||{};

function ptzUrl(command,sequence){
  const url=new URL(api('api/ptz/'+command));
  url.searchParams.set('client',ptzClient);
  url.searchParams.set('seq',String(sequence));
  return url.toString();
}

function setHealth(data){
  const badge=$('headerStatus');
  const status=$('statusBanner');
  badge.className='health-badge '+(data.online&&data.authenticated?'health-online':'health-offline');
  badge.innerHTML='<span class="health-dot"></span><span>'+
    (data.online&&data.authenticated?'Online':data.online?'Online · não autenticada':'Offline')+'</span>';

  if(!data.online){
    status.className='status-banner bad';
    status.textContent='Câmera offline. O painel mantém somente os últimos dados conhecidos.';
  }else if(!data.authenticated){
    status.className='status-banner bad';
    status.textContent='Câmera online, mas a autenticação local ainda não foi validada.';
  }else if(data.probe_running){
    status.className='status-banner';
    status.textContent='Diagnóstico profundo em andamento. PTZ e mídia pesada ficam protegidos contra concorrência.';
  }else if(data.preview_active){
    status.className='status-banner ok';
    status.textContent='Câmera acessível · preview ao vivo ativo · PTZ disponível.';
  }else{
    status.className='status-banner ok';
    status.textContent='Câmera autenticada e acessível pela LAN.';
  }

  const device=data.device_info||{};
  $('headerSubtitle').textContent=[
    device.model||data.lan_support?.model||'Câmera local',
    device.firmware_version?'firmware '+device.firmware_version:null,
    device.timezone||null
  ].filter(Boolean).join(' · ');
}

function statusBadge(enabled,onLabel='Ativo',offLabel='Inativo'){
  return '<span class="badge '+(enabled?'success':'neutral')+'"><span class="state-dot '+(enabled?'state-on':'state-off')+'"></span>'+
    esc(enabled?onLabel:offLabel)+'</span>';
}

function renderOverview(data){
  const device=data.device_info||{};
  const state=device.local_state||{};
  const services=data.services||{};
  const onvif=data.onvif_info||{};
  const media=data.media_probe||{};
  const total=device.sdcard_total_mb;
  const networkRaw=data.network_state?.NETWORKSTATE;
  const networkFriendly=networkRaw==='CONNECTBUTT'?'Conectada':(networkRaw||'Sem estado reportado');

  $('overviewCards').innerHTML=[
    {label:'Dispositivo',value:device.model||data.lan_support?.model||'—',sub:(device.channel_count||'—')+' canais · '+(device.capabilities?.codec||'codec desconhecido')},
    {label:'Rede',value:data.online?'Online':'Offline',sub:networkFriendly},
    {label:'Armazenamento',value:formatMb(total),sub:device.sdcard_free_mb===0?'0 MB livre reportado · sem interpretar como cheio':formatMb(device.sdcard_free_mb)+' livre'},
    {label:'Mídia',value:media.reachable?'RTSP ativo':'Sem RTSP',sub:(media.reported_channel_count||device.channel_count||'—')+' canais · porta '+(media.port||554)}
  ].map(item=>'<div class="stat-card"><span class="label">'+esc(item.label)+'</span><div><div class="value">'+esc(item.value)+'</div><div class="subvalue">'+esc(item.sub)+'</div></div></div>').join('');

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
    ['HTTP',services.http,'80','CGI local'],
    ['RTSP',services.rtsp,'554','Vídeo e áudio'],
    ['ONVIF',services.onvif,'8899','Descoberta read-only'],
    ['API local',services.features,'9898','Capabilities / estado']
  ];
  $('serviceList').innerHTML=serviceDefs.map(([label,item,fallback,desc])=>{
    const ok=!!item?.reachable;
    return '<div class="service-row"><div class="service-left"><div class="service-icon">'+esc(label.slice(0,2))+'</div><div><strong>'+
      esc(label)+'</strong><small>'+esc(desc)+' · porta '+esc(item?.port||fallback)+'</small></div></div>'+
      '<span class="badge '+(ok?'success':'danger')+'">'+(ok?'Disponível':'Indisponível')+'</span></div>';
  }).join('');

  renderSnapshotTiles('overviewMedia',data,false);
}

function availableMainStreams(data){
  const streams=data?.media_probe?.streams||[];
  const result=[];
  for(let channel=0;channel<Math.max(1,Number(data?.media_probe?.reported_channel_count||data?.device_info?.channel_count||1));channel++){
    const prefix='/live/ch'+String(channel).padStart(2,'0')+'_';
    const options=streams.filter(item=>item.available&&item.path?.startsWith(prefix)&&item.streams?.some(s=>s.codec_type==='video'));
    const preferred=options.find(item=>item.path.endsWith('_0'))||options[0];
    if(preferred)result.push({channel,item:preferred});
  }
  return result;
}

function renderSnapshotTiles(rootId,data,withTechnical){
  const root=$(rootId);
  const streams=availableMainStreams(data);
  const signature=JSON.stringify(streams.map(({channel,item})=>[channel,item.path,item.streams]));
  const signatureKey=rootId==='overviewMedia'?'overview':'camera';
  const previous=signatureKey==='overview'?lastOverviewSignature:lastCameraSignature;
  if(signature===previous&&root.children.length)return;
  if(signatureKey==='overview')lastOverviewSignature=signature;else lastCameraSignature=signature;

  root.querySelectorAll('img[data-object-url]').forEach(img=>URL.revokeObjectURL(img.dataset.objectUrl||''));
  root.innerHTML='';
  if(!streams.length){
    root.innerHTML='<div class="empty">Nenhum stream confirmado. Execute o diagnóstico profundo.</div>';
    return;
  }

  for(const {channel,item} of streams){
    const video=videoInfo(item),audio=audioInfo(item);
    const tile=document.createElement('article');
    tile.className='camera-tile';
    tile.innerHTML=
      '<div class="camera-frame"><img data-stream="'+esc(item.path.split('/').pop())+'" alt="Lente '+(channel+1)+'"></div>'+
      '<div class="camera-body"><div class="camera-title"><strong>Lente '+(channel+1)+'</strong><span class="badge success">RTSP</span></div>'+
      '<div class="meta-row"><span class="meta-pill">'+esc(video.width||'—')+'×'+esc(video.height||'—')+'</span>'+
      '<span class="meta-pill">'+esc((video.codec_name||'').toUpperCase()||'—')+'</span>'+
      (audio.codec_name?'<span class="meta-pill">Áudio '+esc(audio.codec_name)+'</span>':'')+
      '</div><div data-snapshot-state class="helper">Aguardando snapshot</div></div>';
    root.appendChild(tile);
  }
  if(
    (rootId==='overviewMedia'&&currentTab==='overview')||
    (rootId==='cameraMedia'&&currentTab==='camera')
  )void refreshSnapshots(root);
}

async function loadSnapshot(img){
  const state=img.closest('.camera-tile')?.querySelector('[data-snapshot-state]');
  try{
    const response=await fetch(api('api/snapshot/'+encodeURIComponent(img.dataset.stream)+'?t='+Date.now()),{cache:'no-store'});
    if(!response.ok){
      if(state)state.textContent=response.status===409?'Snapshot pausado durante vídeo/diagnóstico':'Falha ao atualizar';
      return false;
    }
    const stale=response.headers.get('X-JOOAN-Snapshot')==='stale';
    const blob=await response.blob();
    const objectUrl=URL.createObjectURL(blob);
    const previous=img.dataset.objectUrl;
    img.src=objectUrl;img.dataset.objectUrl=objectUrl;
    if(previous)URL.revokeObjectURL(previous);
    if(state)state.textContent=stale?'Imagem em cache temporário':'Snapshot atualizado agora';
    return true;
  }catch(_){
    if(state)state.textContent='Falha ao atualizar';
    return false;
  }
}

function isVisibleInViewport(element){
  if(!element)return true;
  const rect=element.getBoundingClientRect();
  return rect.bottom>0&&rect.top<window.innerHeight;
}

async function refreshSnapshots(root=null){
  if(document.hidden||snapshotRefreshRunning||lastData?.probe_running||lastData?.preview_active||activeDirection)return;
  if(root&&!isVisibleInViewport(root))return;
  snapshotRefreshRunning=true;
  try{
    const scope=root||document;
    const images=[...scope.querySelectorAll('img[data-stream]')];
    for(let i=0;i<images.length;i++){
      await loadSnapshot(images[i]);
      if(i+1<images.length)await sleep(300);
    }
  }finally{snapshotRefreshRunning=false}
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
    const enabled=boolState(state[key]);
    return '<div class="setting-item"><div><strong>'+esc(label)+'</strong><span>'+esc(sub||(supported?'Suportado':'Não detectado'))+'</span></div>'+
      '<span class="setting-value">'+(enabled?'Ativo':'Inativo')+'</span></div>';
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
    const value=state[key];
    const supported=cap?capabilities[cap]===true:Object.prototype.hasOwnProperty.call(state,key);
    const enabled=boolState(value);
    return '<div class="setting-item"><div><strong>'+esc(label)+'</strong><span>'+(supported?'Estado reportado':'Não detectado')+'</span></div>'+
      '<span class="setting-value">'+(enabled?'Ativo':'Inativo')+'</span></div>';
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
  if(total>0&&free>0&&free<=total)percent=Math.max(0,Math.min(100,((total-free)/total)*100));

  $('storageCard').innerHTML=
    '<div class="storage-hero"><div><span class="helper">Capacidade reportada</span><div class="big">'+esc(formatMb(total))+'</div></div>'+
    '<span class="badge '+(device.capabilities?.sdcard?'success':'neutral')+'">'+(device.capabilities?.sdcard?'SD suportado':'Sem capability')+'</span></div>'+
    '<div class="progress"><span style="width:'+(percent==null?0:percent)+'%"></span></div>'+
    '<div class="info-list"><div class="info-row"><span>Livre reportado</span><strong>'+esc(formatMb(free))+'</strong></div>'+
    '<div class="info-row"><span>Status bruto</span><strong>'+esc(state.sdcard_status??'—')+'</strong></div>'+
    '<div class="info-row"><span>Exceção bruta</span><strong>'+esc(state.sdcard_excepreason??'—')+'</strong></div></div>'+
    (free===0?'<p class="helper">O firmware reporta 0 MB livres. A interface não interpreta isso automaticamente como cartão cheio enquanto o significado do status 0 não estiver validado.</p>':'');

  $('recordingCard').innerHTML=
    '<div class="storage-hero"><div><span class="helper">Gravação local</span><div class="big">'+(boolState(state.record_enable)?'Ligada':'Desligada')+'</div></div>'+
    statusBadge(boolState(state.record_enable))+'</div>'+
    '<div class="info-list"><div class="info-row"><span>Tipo bruto</span><strong>'+esc(state.record_type??props.rectype??'—')+'</strong></div>'+
    '<div class="info-row"><span>Canal reportado</span><strong>'+esc(state.recordechannel??'—')+'</strong></div>'+
    '<div class="info-row"><span>Agenda principal</span><strong>'+esc(scheduleSummary(props.newrecord_schedule||props.record_schedule))+'</strong></div></div>';

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

function renderDiagnostics(data){
  const services=data.services||{};
  const items=[
    ['HTTP',services.http?.reachable,'Porta '+(services.http?.port||80)],
    ['API local',services.features?.reachable,'Porta '+(services.features?.port||9898)],
    ['RTSP',services.rtsp?.reachable,'Porta '+(services.rtsp?.port||554)],
    ['ONVIF',services.onvif?.reachable,'Porta '+(services.onvif?.port||8899)]
  ];
  $('diagnosticSummary').innerHTML=items.map(([label,ok,sub])=>
    '<div class="diagnostic-item"><strong>'+esc(label)+'</strong><span>'+esc(sub)+'</span><div style="margin-top:9px">'+
    '<span class="badge '+(ok?'success':'danger')+'">'+(ok?'Disponível':'Indisponível')+'</span></div></div>'
  ).join('');

  const onvif=data.onvif_info||{};
  const dev=onvif.device_diagnostics?.device_information?.data||{};
  const net=onvif.device_diagnostics?.network_interfaces?.data?.[0]||{};
  const profiles=onvif.profiles||[];
  const ptz=onvif.ptz||{};
  $('onvifSummary').innerHTML=
    '<div class="onvif-grid">'+
    '<div class="diagnostic-item"><strong>'+esc(dev.manufacturer||'ONVIF Device')+'</strong><span>'+esc(dev.model||'Modelo não informado')+' · '+esc(dev.firmware_version||'firmware —')+'</span></div>'+
    '<div class="diagnostic-item"><strong>Rede ONVIF</strong><span>'+esc(net.ipv4?.[0]?.address||'—')+' · MTU '+esc(net.mtu||'—')+'</span></div>'+
    '<div class="diagnostic-item"><strong>Perfis de mídia</strong><span>'+profiles.length+' perfil(is) · '+esc(onvif.media_authentication_required?'autenticação exigida':'sem autenticação nessa leitura')+'</span></div>'+
    '<div class="diagnostic-item"><strong>PTZ ONVIF</strong><span>'+(ptz.nodes?.length||0)+' node(s) · '+(ptz.status?.move_pantilt||'estado —')+'</span></div>'+
    '<div class="diagnostic-item"><strong>Presets</strong><span>'+((ptz.presets||[]).length)+' encontrados · máximo reportado '+esc(ptz.nodes?.[0]?.maximum_presets||'—')+'</span></div>'+
    '<div class="diagnostic-item"><strong>Serviços</strong><span>'+Object.keys(onvif.services||{}).length+' endpoints descobertos</span></div>'+
    '</div>'+
    '<div class="meta-row">'+profiles.map(profile=>'<span class="meta-pill">'+esc(profile.name||profile.token)+' · '+esc(profile.video?.width||'—')+'×'+esc(profile.video?.height||'—')+' · '+esc(profile.video?.frame_rate_limit||'—')+' fps</span>').join('')+'</div>';

  renderRawDetails(data);
}

function appendDetails(root,title,obj){
  const details=document.createElement('details');
  const summary=document.createElement('summary');
  const pre=document.createElement('pre');
  summary.textContent=title;
  pre.textContent=obj==null?'Não disponível':JSON.stringify(obj,null,2);
  details.append(summary,pre);root.appendChild(details);
}

function renderRawDetails(data){
  const root=$('rawDetails');root.innerHTML='';
  appendDetails(root,'Dispositivo',data.device_info);
  appendDetails(root,'ONVIF',data.onvif_info);
  appendDetails(root,'LAN / RTSP',{lan_support:data.lan_support,stream_info:data.stream_info,media_probe:data.media_probe,preview_probe:data.preview_probe});
  appendDetails(root,'Plataforma',data.camera_info);
  appendDetails(root,'Rede',{online:data.online,last_seen:data.last_seen,last_heartbeat:data.last_heartbeat,heartbeat_error:data.heartbeat_error,network_state:data.network_state});
}

function bestPreviewDescriptor(data,channel){
  const channelId='ch'+String(channel).padStart(2,'0');
  const validated=(data.preview_probe?.streams||[]).find(item=>item.path===('/live/'+channelId+'_1')&&item.available&&item.streams?.some(s=>s.codec_type==='video'));
  if(validated)return {path:validated.path,source:'Substream validado',video:videoInfo(validated)};
  const profile=(data.onvif_info?.profiles||[]).find(p=>p.stream?.path===('/live/'+channelId+'_1'));
  if(profile)return {path:profile.stream.path,source:'Substream ONVIF',video:profile.video||{}};
  const main=(data.media_probe?.streams||[]).find(item=>item.path===('/live/'+channelId+'_0')&&item.available);
  if(main)return {path:main.path,source:'Stream principal',video:videoInfo(main)};
  return {path:'/live/'+channelId+'_0',source:'Candidato',video:{}};
}

function renderLiveMeta(data){
  if(Number.isInteger(data.ptz_channel))selectedChannel=data.ptz_channel;
  const descriptor=bestPreviewDescriptor(data,selectedChannel);
  const video=descriptor.video||{};
  $('liveTitle').textContent='Lente PTZ · Lente '+(selectedChannel+1);
  $('ptzLensBadge').textContent='PTZ → ch'+String(selectedChannel).padStart(2,'0');
  $('ptzLensBadge').className='badge '+(data.ptz_channel_source==='onvif_profile_mapping'?'success':'warning');
  $('liveMeta').innerHTML=[
    descriptor.source,
    descriptor.path,
    video.width&&video.height?video.width+'×'+video.height:null,
    video.frame_rate_limit?video.frame_rate_limit+' fps':null,
    video.bitrate_limit_kbps?video.bitrate_limit_kbps+' kbps':null,
    video.codec_name?(String(video.codec_name).toUpperCase()):video.encoding||null
  ].filter(Boolean).map(value=>'<span class="meta-pill">'+esc(value)+'</span>').join('');
  $('ptzState').textContent=data.ptz_moving?'Em movimento':'Parado';
  $('ptzState').className='badge '+(data.ptz_moving?'warning':'neutral');
}

function enablePtz(enabled){
  document.querySelectorAll('[data-dir]').forEach(button=>button.disabled=!enabled);
  $('stop').disabled=!enabled;
}

async function sendPtz(command,keepalive=false){
  if(!authenticated)return;
  const sequence=++ptzSequence;
  try{
    const response=await fetch(ptzUrl(command,sequence),{method:'POST',keepalive});
    const body=await response.json();
    if(!body.ignored)$('command').textContent=response.ok?'Comando: '+command:'Erro: '+(body.error||'falha');
    if(!response.ok&&response.status===401){authenticated=false;enablePtz(false)}
  }catch(error){$('command').textContent='Erro: '+error.message}
}

function emergencyStop(){
  if(!activeDirection)return;
  activeDirection=null;
  void sendPtz('stop',true);
}

async function startLivePreview(){
  if(!lastData?.online||!lastData?.authenticated){$('previewMessage').textContent='A câmera precisa estar online e autenticada.';return}
  liveActive=true;
  $('singleSnapshot').hidden=true;
  $('previewEmpty').hidden=true;
  $('liveImage').hidden=false;
  $('liveIndicator').hidden=false;
  $('startLive').disabled=true;
  $('stopLive').disabled=false;
  $('previewMessage').textContent='Abrindo preview local...';
  $('liveImage').src=api('api/live/ptz?t='+Date.now());
}

async function stopLivePreview(){
  liveActive=false;
  $('liveImage').removeAttribute('src');
  $('liveImage').hidden=true;
  $('liveIndicator').hidden=true;
  $('startLive').disabled=false;
  $('stopLive').disabled=true;
  try{await fetch(api('api/live/stop'),{method:'POST',keepalive:true})}catch(_){}
  if(!$('singleSnapshot').hasAttribute('src'))$('previewEmpty').hidden=false;
  if($('previewMessage').textContent.includes('preview'))$('previewMessage').textContent='Preview parado.';
}

async function loadSingleSnapshot(){
  if(liveActive)await stopLivePreview();
  const stream='ch'+String(selectedChannel).padStart(2,'0')+'_0';
  $('previewMessage').textContent='Carregando snapshot...';
  try{
    const response=await fetch(api('api/snapshot/'+stream+'?t='+Date.now()),{cache:'no-store'});
    if(!response.ok)throw new Error('HTTP '+response.status);
    const blob=await response.blob();
    const url=URL.createObjectURL(blob);
    const previous=$('singleSnapshot').dataset.objectUrl;
    $('singleSnapshot').src=url;$('singleSnapshot').dataset.objectUrl=url;$('singleSnapshot').hidden=false;
    $('previewEmpty').hidden=true;
    if(previous)URL.revokeObjectURL(previous);
    $('previewMessage').textContent=response.headers.get('X-JOOAN-Snapshot')==='stale'?'Snapshot em cache temporário.':'Snapshot atualizado.';
  }catch(error){$('previewMessage').textContent='Falha no snapshot: '+error.message}
}

async function validateSubstreams(){
  const button=$('validateSubstreams');button.disabled=true;button.textContent='Validando...';
  try{
    const response=await fetch(api('api/preview/validate'),{method:'POST'});
    if(!response.ok){const body=await response.json();throw new Error(body.error||('HTTP '+response.status))}
    for(let i=0;i<20;i++){
      await sleep(1000);await refreshStatus();
      if(!lastData?.preview_probe_running)break;
    }
    $('previewMessage').textContent='Validação do stream PTZ concluída.';
  }catch(error){$('previewMessage').textContent='Validação: '+error.message}
  finally{button.disabled=!!lastData?.preview_probe_running;button.textContent=lastData?.preview_probe_running?'Validando...':'Validar stream PTZ'}
}

async function deepProbe(){
  if(liveActive)await stopLivePreview();
  const button=$('probe');button.disabled=true;button.textContent='Diagnosticando...';
  try{
    const response=await fetch(api('api/probe'),{method:'POST'});
    if(!response.ok){const body=await response.json();throw new Error(body.error||('HTTP '+response.status))}
    for(let i=0;i<90;i++){await sleep(1000);await refreshStatus();if(!lastData?.probe_running)break}
  }catch(error){$('statusBanner').textContent='Diagnóstico: '+error.message}
  finally{button.disabled=!!lastData?.probe_running;button.textContent=lastData?.probe_running?'Diagnosticando...':'Executar diagnóstico profundo'}
}

async function lightTest(){
  const button=$('lightTest');button.disabled=true;
  try{
    const response=await fetch(api('api/test'),{method:'POST'});
    if(!response.ok){const body=await response.json();throw new Error(body.busy?'Câmera ocupada':(body.error||('HTTP '+response.status)))}
    await refreshStatus();
  }catch(error){$('statusBanner').textContent='Validação CGI: '+error.message}
  finally{button.disabled=false}
}

function showLabResult(value){
  const target=$('labResult');
  if(!target)return;
  target.textContent=typeof value==='string'?value:JSON.stringify(value,null,2);
}

async function labRequest(path,body=null){
  if(labRequestRunning)throw new Error('Outro teste do laboratório está em andamento');
  labRequestRunning=true;
  try{
    const options={method:'POST',headers:{}};
    if(body!==null){
      options.headers['Content-Type']='application/json';
      options.body=JSON.stringify(body);
    }
    const response=await fetch(api(path),options);
    let payload={};
    try{payload=await response.json()}catch(_){payload={error:'Resposta inválida do backend'}}
    showLabResult(payload);
    if(!response.ok)throw new Error(payload.error||('HTTP '+response.status));
    return payload;
  }finally{
    labRequestRunning=false;
  }
}

function labPresetListFromPayload(payload){
  if(Array.isArray(payload?.presets))return payload.presets;
  if(Array.isArray(payload?.readback?.presets))return payload.readback.presets;
  return null;
}

function selectedLabPreset(){
  const token=$('labPresetSelect')?.value||'';
  return labPresets.find(item=>String(item?.token??'')===token)||null;
}

function renderLabPresetSelect(){
  const select=$('labPresetSelect');
  if(!select)return;
  const previous=select.value;
  select.innerHTML='';
  if(!labPresets.length){
    const option=document.createElement('option');
    option.value='';
    option.textContent='Nenhum preset listado';
    select.appendChild(option);
    select.disabled=true;
    return;
  }
  for(const preset of labPresets){
    const token=String(preset?.token??'');
    if(!token)continue;
    const option=document.createElement('option');
    option.value=token;
    option.textContent=(preset?.name||'Sem nome')+' · '+token;
    select.appendChild(option);
  }
  select.disabled=select.options.length===0;
  if([...select.options].some(option=>option.value===previous))select.value=previous;
}

function updateLabPresetButtons(){
  if(!$('labPresetGoto'))return;
  const preset=selectedLabPreset();
  $('labPresetGoto').disabled=!preset||labRequestRunning;
  $('labPresetDelete').disabled=!(preset?.name==='HA_TEST')||labRequestRunning;
  $('labPresetSummary').textContent=labPresets.length
    ?labPresets.length+' preset(s) retornado(s) pela câmera. Selecione um para testar GotoPreset.'
    :'Nenhum preset ONVIF retornado pela câmera.';
}

function renderLabInventory(data){
  const root=$('labOemInventory');
  if(!root)return;
  const state=data?.device_info?.local_state||{};
  const defs=[
    ['Motion','md_enable'],
    ['Sensibilidade motion','mdsensitivity'],
    ['Motion secundário','sub_md_enable'],
    ['Auto tracking','autotrack'],
    ['Detecção de pessoa','person_detect'],
    ['Detecção de veículo','vehicle_detect'],
    ['Person tracking','person_track_enable'],
    ['LED','led'],
    ['Floodlight','floodlight'],
    ['Luz amarela','yellowlight'],
    ['Flip/mirror','flipmirror'],
    ['Gravação','record_enable'],
    ['Tipo de gravação','record_type'],
    ['Privacy / PTZ hide','ptz_hide_mode'],
    ['Buzzer','buzzer'],
    ['Push da câmera','msgpush_enable'],
    ['Sensibilidade de áudio','audiosensitive'],
    ['Frequência elétrica','powerfrequency'],
    ['Qualidade','qualitymode']
  ];
  root.innerHTML=defs.map(([label,key])=>{
    const exists=Object.prototype.hasOwnProperty.call(state,key);
    const value=exists?state[key]:'—';
    return '<div class="setting-item"><div><strong>'+esc(label)+'</strong><span>'+esc(key)+' · setter pendente</span></div>'+
      '<span class="setting-value">'+esc(value)+'</span></div>';
  }).join('');
}

function updateLabAvailability(data){
  const ready=!!data?.online&&!!data?.authenticated&&!data?.probe_running&&!data?.preview_active&&!data?.preview_probe_running&&!data?.ptz_moving&&!labRequestRunning;
  document.querySelectorAll('#tab-lab button').forEach(button=>{
    if(button.id==='labClearResult')return;
    if(button.id==='labPresetGoto'||button.id==='labPresetDelete')return;
    button.disabled=!ready;
  });

  const recordingCompare=$('labOemRecordingCompare');
  if(recordingCompare)recordingCompare.disabled=!ready||!labOemRecordingBaseline;

  const toggleCompare=$('labOemToggleCompare');
  const selectedGroup=$('labOemToggleGroup')?.value||'';
  if(toggleCompare){
    toggleCompare.disabled=!ready||!labOemToggleBaseline||labOemToggleBaseline.group!==selectedGroup;
  }

  updateLabPresetButtons();
}

async function labPtz(direction){
  const speed=Number($('labPtzSpeed').value);
  const duration=Number($('labPtzDuration').value);
  try{
    await labRequest('api/lab/onvif/ptz',{direction,speed,duration_ms:duration});
  }catch(error){showLabResult('PTZ ONVIF: '+error.message)}
  finally{if(lastData)updateLabAvailability(lastData)}
}

async function labIr(enabled){
  try{await labRequest('api/lab/onvif/ir',{enabled})}
  catch(error){showLabResult('IR: '+error.message)}
  finally{if(lastData)updateLabAvailability(lastData)}
}

async function labPresetList(){
  if(labRequestRunning)return;
  labRequestRunning=true;
  try{
    const response=await fetch(api('api/lab/onvif/presets'),{cache:'no-store'});
    const payload=await response.json();
    showLabResult(payload);
    if(!response.ok)throw new Error(payload.error||('HTTP '+response.status));
    const presets=labPresetListFromPayload(payload);
    if(presets)labPresets=presets;
    renderLabPresetSelect();
  }catch(error){showLabResult('Presets: '+error.message)}
  finally{
    labRequestRunning=false;
    updateLabPresetButtons();
    if(lastData)updateLabAvailability(lastData);
  }
}

async function labPresetAction(action){
  try{
    const body={action};
    if(action==='goto'||action==='delete'){
      const preset=selectedLabPreset();
      if(!preset)throw new Error('Selecione um preset retornado pela câmera');
      if(action==='delete'&&preset.name!=='HA_TEST')throw new Error('Somente HA_TEST pode ser excluído');
      body.token=String(preset.token);
    }
    const payload=await labRequest('api/lab/onvif/presets',body);
    const presets=labPresetListFromPayload(payload);
    if(presets){
      labPresets=presets;
      renderLabPresetSelect();
    }
  }catch(error){showLabResult('Preset '+action+': '+error.message)}
  finally{
    updateLabPresetButtons();
    if(lastData)updateLabAvailability(lastData);
  }
}

async function labImaging(){
  try{await labRequest('api/lab/onvif/imaging',{action:'discover'})}
  catch(error){showLabResult('Imaging: '+error.message)}
  finally{if(lastData)updateLabAvailability(lastData)}
}

async function labImagingPathProbe(){
  try{
    showLabResult('Comparando os caminhos ONVIF Imaging anunciados pela câmera...');
    await labRequest('api/lab/onvif/imaging',{action:'path_probe'});
  }catch(error){showLabResult('Imaging path probe: '+error.message)}
  finally{if(lastData)updateLabAvailability(lastData)}
}

async function labImagingSet(){
  try{
    const setting=$('labImagingSetting').value;
    const value=Number($('labImagingValue').value);
    if(!Number.isInteger(value)||value<1||value>255)throw new Error('Use um valor inteiro entre 1 e 255');
    await labRequest('api/lab/onvif/imaging',{action:'set',setting,value});
  }catch(error){showLabResult('Imaging write: '+error.message)}
  finally{if(lastData)updateLabAvailability(lastData)}
}

async function labEvents(action){
  try{
    const seconds=Number($('labEventsDuration')?.value||5);
    if(action==='pull')showLabResult('Escutando eventos ONVIF por até '+seconds+' segundos...');
    await labRequest('api/lab/onvif/events',action==='pull'?{action,seconds}:{action});
  }catch(error){showLabResult('Eventos ONVIF: '+error.message)}
  finally{if(lastData)updateLabAvailability(lastData)}
}

async function labStorage(action='discover'){
  try{
    if(action==='playback_probe'){
      showLabResult('Consultando informações da gravação e solicitando URI de replay sem abrir o stream...');
      await labRequest('api/lab/onvif/storage',{action});
    }else if(action==='jobs'){
      showLabResult('Lendo jobs ONVIF existentes, configuração e estado...');
      await labRequest('api/lab/onvif/storage',{action});
    }else if(action==='record_pulse'){
      showLabResult('Testando gravação ONVIF por até 5 segundos e restaurando o job para Idle...');
      await labRequest('api/lab/onvif/storage',{action,seconds:5});
    }else{
      showLabResult('Consultando serviços ONVIF Recording / Search / Replay...');
      await labRequest('api/lab/onvif/storage',{action:'discover'});
    }
  }catch(error){showLabResult('Recording/Search/Replay: '+error.message)}
  finally{if(lastData)updateLabAvailability(lastData)}
}

function oemRecordingComparable(payload){
  return {
    ...(payload?.properties||{}),
    playback_fast_forward:payload?.playback_fast_forward??null,
    channel_count:payload?.channel_count??null
  };
}

function diffPlainObjects(before,after){
  const changes={};
  const keys=new Set([...Object.keys(before||{}),...Object.keys(after||{})]);
  for(const key of keys){
    const left=before?.[key];
    const right=after?.[key];
    if(JSON.stringify(left)!==JSON.stringify(right)){
      changes[key]={before:left??null,after:right??null};
    }
  }
  return changes;
}

async function labOemRecording(action){
  try{
    const payload=await labRequest('api/lab/oem/recording');
    const current=oemRecordingComparable(payload);
    if(action==='baseline'){
      labOemRecordingBaseline=current;
      $('labOemRecordingSummary').textContent='Linha de base capturada. Agora altere uma única opção no CAM720 e clique em comparar.';
      showLabResult({operation:'oem_recording_baseline',baseline:current,raw:payload});
      return;
    }
    if(action==='compare'){
      if(!labOemRecordingBaseline)throw new Error('Capture uma linha de base primeiro');
      const changes=diffPlainObjects(labOemRecordingBaseline,current);
      $('labOemRecordingSummary').textContent=Object.keys(changes).length
        ?Object.keys(changes).length+' campo(s) alterado(s) desde a linha de base.'
        :'Nenhuma alteração detectada nos campos OEM de gravação.';
      showLabResult({
        operation:'oem_recording_compare',
        changes,
        baseline:labOemRecordingBaseline,
        current
      });
      return;
    }
    $('labOemRecordingSummary').textContent='Estado OEM lido. Use linha de base + comparar para mapear mudanças do CAM720.';
  }catch(error){
    showLabResult('OEM recording: '+error.message);
  }finally{
    if(lastData)updateLabAvailability(lastData);
  }
}

function renderOemToggleValues(values){
  const root=$('labOemToggleCurrent');
  if(!root)return;
  const entries=Object.entries(values||{});
  root.innerHTML=entries.length
    ?entries.map(([key,value])=>
      '<div class="setting-item"><div><strong>'+esc(key)+'</strong><span>estado OEM atual</span></div>'+
      '<span class="setting-value">'+esc(typeof value==='object'?JSON.stringify(value):value)+'</span></div>'
    ).join('')
    :'<div class="setting-item"><div><strong>Nenhum campo retornado</strong><span>Este firmware não expôs propriedades do grupo selecionado.</span></div></div>';
}

async function labOemToggle(action){
  const group=$('labOemToggleGroup').value;
  try{
    const payload=await labRequest('api/lab/oem/toggles',{group});
    const current={...(payload?.values||{})};
    renderOemToggleValues(current);

    if(action==='baseline'){
      labOemToggleBaseline={group,values:current};
      $('labOemToggleSummary').textContent=
        'Linha de base de '+group+' capturada. Altere uma única opção no CAM720 e clique em comparar.';
      showLabResult({
        operation:'oem_toggle_baseline',
        group,
        baseline:current,
        keys:payload?.keys||[]
      });
      return;
    }

    if(action==='compare'){
      if(!labOemToggleBaseline)throw new Error('Capture uma linha de base primeiro');
      if(labOemToggleBaseline.group!==group){
        throw new Error('O grupo mudou desde a linha de base; capture uma nova linha de base');
      }
      const changes=diffPlainObjects(labOemToggleBaseline.values,current);
      $('labOemToggleSummary').textContent=Object.keys(changes).length
        ?Object.keys(changes).length+' campo(s) alterado(s) em '+group+'.'
        :'Nenhuma alteração detectada no grupo '+group+'.';
      showLabResult({
        operation:'oem_toggle_compare',
        group,
        changes,
        baseline:labOemToggleBaseline.values,
        current,
        keys:payload?.keys||[]
      });
      return;
    }

    $('labOemToggleSummary').textContent=
      'Grupo '+group+' lido. Capture uma linha de base antes de alterar uma opção no CAM720.';
  }catch(error){
    showLabResult('OEM toggle mapper: '+error.message);
  }finally{
    if(lastData)updateLabAvailability(lastData);
  }
}

function resetOemToggleBaseline(){
  labOemToggleBaseline=null;
  $('labOemToggleSummary').textContent='Grupo alterado. Capture a baseline deste grupo antes de comparar.';
  $('labOemToggleCurrent').innerHTML='';
  if(lastData)updateLabAvailability(lastData);
}

async function labDiagProbe(){
  try{
    showLabResult('Aguardando callback seguro da câmera...');
    await labRequest('api/lab/diag/probe');
  }catch(error){showLabResult('SetDiagMode callback: '+error.message)}
  finally{if(lastData)updateLabAvailability(lastData)}
}

async function labDiagDisable(){
  try{await labRequest('api/lab/diag/disable')}
  catch(error){showLabResult('SetDiagMode OFF: '+error.message)}
  finally{if(lastData)updateLabAvailability(lastData)}
}

function renderAll(data){
  setHealth(data);
  renderOverview(data);
  renderDetection(data);
  renderRecording(data);
  renderDiagnostics(data);
  renderLabInventory(data);
  updateLabAvailability(data);
  renderLiveMeta(data);
  renderSnapshotTiles('cameraMedia',data,true);
  enablePtz(data.online&&data.authenticated&&!data.probe_running);
  $('probe').disabled=!!data.probe_running||!!data.preview_active||!!data.ptz_moving;
  $('probe').textContent=data.probe_running?'Diagnosticando...':'Executar diagnóstico profundo';
  $('validateSubstreams').disabled=!!data.preview_probe_running||!!data.preview_active||!!data.probe_running||!!data.ptz_moving;
  $('validateSubstreams').textContent=data.preview_probe_running?'Validando...':'Validar stream PTZ';
  if(data.preview_last_error&&!data.preview_active&&currentTab==='camera'){
    $('previewMessage').textContent='Preview: '+data.preview_last_error;
  }
  if(data.last_error&&!data.probe_running)$('command').textContent='Último erro: '+data.last_error;
  if(data.preview_active){
    liveActive=true;
    $('startLive').disabled=true;$('stopLive').disabled=false;
  }
}

async function refreshStatus(){
  try{
    const response=await fetch(api('api/status'),{cache:'no-store'});
    if(!response.ok)throw new Error('HTTP '+response.status);
    const data=await response.json();
    lastData=data;online=!!data.online;authenticated=!!data.authenticated;
    renderAll(data);
  }catch(error){
    authenticated=false;enablePtz(false);
    $('statusBanner').className='status-banner bad';
    $('statusBanner').textContent='App/API indisponível: '+error.message;
  }
}

function switchTab(name){
  currentTab=name;
  document.querySelectorAll('.tab').forEach(button=>button.classList.toggle('active',button.dataset.tab===name));
  document.querySelectorAll('.tab-panel').forEach(panel=>panel.classList.toggle('active',panel.id==='tab-'+name));
  if(name!=='camera'&&liveActive)void stopLivePreview();
  if(name==='camera'&&!liveActive&&lastData?.online&&lastData?.authenticated){
    void startLivePreview();
  }else if(name==='overview'&&!liveActive){
    void refreshSnapshots($('overviewMedia'));
  }
}

function stopPolling(){
  if(statusTimer){clearInterval(statusTimer);statusTimer=null}
  if(snapshotTimer){clearInterval(snapshotTimer);snapshotTimer=null}
}
function startPolling(){
  stopPolling();
  if(document.hidden)return;
  void refreshStatus();
  statusTimer=setInterval(()=>{if(!document.hidden)void refreshStatus()},STATUS_REFRESH_MS);
  snapshotTimer=setInterval(()=>{
    if(!document.hidden&&!liveActive&&(currentTab==='overview'||currentTab==='camera'))void refreshSnapshots(currentTab==='overview'?$('overviewMedia'):$('cameraMedia'));
  },SNAPSHOT_REFRESH_MS);
}

document.querySelectorAll('.tab').forEach(button=>button.addEventListener('click',()=>switchTab(button.dataset.tab)));
const directionButtons=[...document.querySelectorAll('[data-dir]')];
directionButtons.forEach(button=>{
  const direction=button.dataset.dir;
  button.addEventListener('pointerdown',event=>{
    event.preventDefault();
    activeDirection=direction;
    button.setPointerCapture?.(event.pointerId);
    void sendPtz(direction);
  });
  const stop=()=>{
    if(activeDirection===direction){activeDirection=null;void sendPtz('stop')}
  };
  button.addEventListener('pointerup',stop);
  button.addEventListener('pointercancel',stop);
  button.addEventListener('lostpointercapture',stop);
});
$('stop').addEventListener('click',()=>{activeDirection=null;void sendPtz('stop')});
$('startLive').addEventListener('click',startLivePreview);
$('stopLive').addEventListener('click',stopLivePreview);
$('singleSnapshotButton').addEventListener('click',loadSingleSnapshot);
$('validateSubstreams').addEventListener('click',validateSubstreams);
$('probe').addEventListener('click',deepProbe);
$('lightTest').addEventListener('click',lightTest);
$('overviewRefreshSnapshots').addEventListener('click',()=>refreshSnapshots($('overviewMedia')));
$('cameraRefreshSnapshots').addEventListener('click',()=>refreshSnapshots($('cameraMedia')));
document.querySelectorAll('[data-lab-ptz]').forEach(button=>button.addEventListener('click',()=>labPtz(button.dataset.labPtz)));
$('labIrOn').addEventListener('click',()=>labIr(true));
$('labIrOff').addEventListener('click',()=>labIr(false));
$('labPresetList')?.addEventListener('click',labPresetList);
$('labPresetCreate')?.addEventListener('click',()=>labPresetAction('create'));
$('labPresetGoto')?.addEventListener('click',()=>labPresetAction('goto'));
$('labPresetDelete')?.addEventListener('click',()=>labPresetAction('delete'));
$('labPresetSelect')?.addEventListener('change',updateLabPresetButtons);
$('labImagingDiscover')?.addEventListener('click',labImaging);
$('labImagingPathProbe')?.addEventListener('click',labImagingPathProbe);
$('labImagingSet')?.addEventListener('click',labImagingSet);
$('labEventsDiscover').addEventListener('click',()=>labEvents('discover'));
$('labEventsPull').addEventListener('click',()=>labEvents('pull'));
$('labStorageDiscover')?.addEventListener('click',()=>labStorage('discover'));
$('labStorageJobs')?.addEventListener('click',()=>labStorage('jobs'));
$('labStorageRecordPulse')?.addEventListener('click',()=>labStorage('record_pulse'));
$('labStoragePlaybackProbe')?.addEventListener('click',()=>labStorage('playback_probe'));
$('labOemRecordingRead').addEventListener('click',()=>labOemRecording('read'));
$('labOemRecordingBaseline').addEventListener('click',()=>labOemRecording('baseline'));
$('labOemRecordingCompare').addEventListener('click',()=>labOemRecording('compare'));
$('labOemToggleRead').addEventListener('click',()=>labOemToggle('read'));
$('labOemToggleBaseline').addEventListener('click',()=>labOemToggle('baseline'));
$('labOemToggleCompare').addEventListener('click',()=>labOemToggle('compare'));
$('labOemToggleGroup').addEventListener('change',resetOemToggleBaseline);
$('labDiagProbe').addEventListener('click',labDiagProbe);
$('labDiagDisable').addEventListener('click',labDiagDisable);
$('labClearResult').addEventListener('click',()=>showLabResult('Nenhum teste executado.'));

$('liveImage').addEventListener('load',()=>{$('previewMessage').textContent='Preview ao vivo ativo.'});
$('liveImage').addEventListener('error',()=>{
  if(liveActive)$('previewMessage').textContent='O preview PTZ foi interrompido. O backend tentará o stream principal se o substream não gerar vídeo.';
});

window.addEventListener('blur',emergencyStop);
document.addEventListener('visibilitychange',()=>{
  if(document.hidden){emergencyStop();void stopLivePreview();stopPolling()}
  else startPolling();
});
window.addEventListener('pagehide',()=>{emergencyStop();void stopLivePreview();stopPolling()});
window.addEventListener('pageshow',()=>{if(!document.hidden)startPolling()});

enablePtz(false);
startPolling();

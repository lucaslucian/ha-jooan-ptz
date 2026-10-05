'use strict';

let lastData=null;
let authenticated=false;
let activeDirection=null;
let statusTimer=null;
let ptzSequence=0;
let ptzHoldGeneration=0;
const liveChannels=new Set();
const pendingChannels=new Set();
const snapshotUrls={0:null,1:null};
const STATUS_REFRESH_MS=5000;

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
    banner.textContent='Offline · mantendo os últimos dados conhecidos e bloqueando novas sessões.';
  }else if(!data.authenticated){
    banner.className='status-banner warning';
    banner.textContent='LAN acessível · autenticação CGI ainda não validada.';
  }else if(data.probe_running){
    banner.className='status-banner warning';
    banner.textContent='Descoberta ONVIF em andamento · mídia e PTZ protegidos contra concorrência.';
  }else if(pendingChannels.size){
    banner.className='status-banner warning';
    banner.textContent='Abrindo '+pendingChannels.size+' feed(s) RTSP · aguarde a negociação terminar.';
  }else if(liveChannels.size){
    banner.className='status-banner ok';
    banner.textContent=liveChannels.size+' feed(s) ao vivo · PTZ continua disponível.';
  }else{
    banner.className='status-banner ok';
    banner.textContent='Autenticada · PTZ pronto · RTSP somente sob demanda.';
  }

  const device=data.device_info||{};
  const model=device.model||data.lan_support?.model||'Câmera local';
  $('headerTitle').textContent=model;
  const meta=[
    device.firmware_version?['FW',device.firmware_version]:null,
    device.timezone?['Fuso',device.timezone]:null,
    data.authenticated?['Auth','OK']:null,
    liveChannels.size?['RTSP',liveChannels.size+' ativo(s)']:pendingChannels.size?['RTSP','abrindo']:['RTSP','sob demanda']
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
      sub:'porta '+(services.rtsp?.port||554)+' · validação somente sob demanda'
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
    ['RTSP',services.rtsp,'554','Mídia sob demanda'],
    ['ONVIF',services.onvif,'8899','Descoberta manual']
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

function bestPreviewDescriptor(data,channel){
  const channelId='ch'+String(channel).padStart(2,'0');
  const active=data.preview_streams?.[String(channel)];
  const preview=(data.preview_probe?.streams||[]).find(item=>item.path===('/live/'+channelId+'_1'));
  const profile=(data.onvif_info?.profiles||[]).find(item=>item.stream?.path===('/live/'+channelId+'_1'));
  const main=(data.media_probe?.streams||[]).find(item=>item.path===('/live/'+channelId+'_0'));

  const path=active?.path||preview?.path||profile?.stream?.path||main?.path||('/live/'+channelId+'_1');
  let video={};
  if(preview?.path===path)video=videoInfo(preview);
  else if(profile?.stream?.path===path)video=profile.video||{};
  else if(main?.path===path)video=videoInfo(main);

  return {
    path,
    source:active?.source||(preview?.available?'Substream validado':profile?'Substream ONVIF':'Baixa resolução candidata'),
    video
  };
}

function renderFeedMeta(data,channel){
  const descriptor=bestPreviewDescriptor(data,channel);
  const video=descriptor.video||{};
  const meta=$('feedMeta'+channel);
  meta.innerHTML=[
    descriptor.source,
    descriptor.path,
    video.width&&video.height?video.width+'×'+video.height:null,
    video.frame_rate_limit?video.frame_rate_limit+' fps':null,
    video.codec_name?String(video.codec_name).toUpperCase():video.encoding||null
  ].filter(Boolean).map(value=>'<span class="meta-pill">'+esc(value)+'</span>').join('');
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
    preview_probe:data.preview_probe||null,
    active_preview_channels:data.preview_channels||[]
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

function appendDetails(root,title,obj){
  const details=document.createElement('details');
  const summary=document.createElement('summary');
  const pre=document.createElement('pre');
  summary.textContent=title;
  pre.textContent=obj==null?'Não disponível':JSON.stringify(obj,null,2);
  details.append(summary,pre);
  root.appendChild(details);
}

function renderRawDetails(data){
  const root=$('rawDetails');
  root.innerHTML='';
  appendDetails(root,'Dispositivo',data.device_info);
  appendDetails(root,'ONVIF',data.onvif_info);
  appendDetails(root,'RTSP',{stream_info:data.stream_info,preview_probe:data.preview_probe,preview_streams:data.preview_streams});
  appendDetails(root,'Rede',{online:data.online,last_seen:data.last_seen,last_heartbeat:data.last_heartbeat,heartbeat_error:data.heartbeat_error,network_state:data.network_state});
}

function renderDiagnostics(data){
  const services=data.services||{};
  const defs=[
    ['HTTP',services.http,'Porta '+(services.http?.port||80)],
    ['API local',services.features,'Porta '+(services.features?.port||9898)],
    ['RTSP',services.rtsp,'Porta '+(services.rtsp?.port||554)],
    ['ONVIF',services.onvif,'Porta '+(services.onvif?.port||8899)]
  ];
  $('diagnosticSummary').innerHTML=defs.map(([label,item,sub])=>{
    let className='neutral',text='Não testado';
    if(item?.reachable===true){className='success';text='Disponível'}
    else if(item?.reachable===false){className='danger';text='Indisponível'}
    else if(label==='RTSP'&&item?.configured){className='warning';text='Configurado'}
    return '<div class="diagnostic-item"><strong>'+esc(label)+'</strong><span>'+esc(sub)+
      '</span><div style="margin-top:9px"><span class="badge '+className+'">'+esc(text)+'</span></div></div>';
  }).join('');

  const onvif=data.onvif_info||{};
  const ptz=onvif.ptz||{};
  const profiles=onvif.profiles||[];
  const dev=onvif.device_diagnostics?.device_information?.data||{};
  if(!onvif.reachable){
    $('onvifSummary').innerHTML='<div class="empty">ONVIF ainda não foi necessário. A descoberta mínima ocorre somente ao usar o PTZ.</div>';
  }else{
    $('onvifSummary').innerHTML=
      '<div class="onvif-grid">'+
      '<div class="diagnostic-item"><strong>'+esc(dev.manufacturer||'ONVIF Device')+'</strong><span>'+
      esc(dev.model||'Modelo não informado')+'</span></div>'+
      '<div class="diagnostic-item"><strong>Perfis</strong><span>'+profiles.length+' perfil(is)</span></div>'+
      '<div class="diagnostic-item"><strong>PTZ</strong><span>'+(ptz.profile_token?'Perfil '+esc(ptz.profile_token):'Não descoberto')+'</span></div>'+
      '</div>'+
      '<div class="meta-row">'+profiles.map(profile=>
        '<span class="meta-pill">'+esc(profile.name||profile.token)+' · '+esc(profile.video?.width||'—')+'×'+
        esc(profile.video?.height||'—')+'</span>'
      ).join('')+'</div>';
  }
  renderRawDetails(data);
}

function renderControl(data){
  renderFeedMeta(data,0);
  renderFeedMeta(data,1);
  renderGeneralInfo(data);

  $('ptzState').textContent=data.ptz_moving?'Em movimento':'Parado';
  $('ptzState').className='badge '+(data.ptz_moving?'warning':'neutral');

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
  const ptzEnabled=!!data.online&&!!data.authenticated&&!data.probe_running;
  document.querySelectorAll('[data-dir]').forEach(button=>button.disabled=!ptzEnabled);
  $('stop').disabled=!ptzEnabled;
  $('ptzSpeed').disabled=!ptzEnabled;

  const mediaEnabled=!!data.online&&!!data.authenticated&&!data.probe_running&&!data.preview_probe_running;
  const mediaBusy=pendingChannels.size>0;
  $('startLive').disabled=!mediaEnabled||mediaBusy||liveChannels.size===2;
  $('stopLive').disabled=liveChannels.size===0&&pendingChannels.size===0;
  $('refreshSnapshots').disabled=!mediaEnabled||mediaBusy||liveChannels.size>0;
  document.querySelectorAll('[data-live-channel]').forEach(button=>{
    const channel=Number(button.dataset.liveChannel);
    button.disabled=!mediaEnabled||mediaBusy||liveChannels.has(channel)||pendingChannels.has(channel);
  });
  document.querySelectorAll('[data-stop-channel]').forEach(button=>{
    const channel=Number(button.dataset.stopChannel);
    button.disabled=!liveChannels.has(channel)&&!pendingChannels.has(channel);
  });
  document.querySelectorAll('[data-snapshot-channel]').forEach(button=>{
    button.disabled=!mediaEnabled||mediaBusy||liveChannels.size>0;
  });


}

function renderAll(data){
  setHealth(data);
  renderOverview(data);
  renderControl(data);
  renderDetection(data);
  renderRecording(data);
  renderDiagnostics(data);
  renderAllReadSettings(data);
  enableControls(data);

  if(data.preview_last_error&&liveChannels.size===0){
    $('previewMessage').textContent='Último erro de preview: '+data.preview_last_error;
  }
  if(data.last_error&&!data.probe_running){
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
    pendingChannels.clear();
    $('headerStatus').className='health-badge health-offline';
    $('headerStatus').innerHTML='<span class="health-dot"></span><span>App indisponível</span>';
    $('statusBanner').className='status-banner bad';
    $('statusBanner').textContent='App/API indisponível: '+error.message;
  }
}

function setFeedUi(channel,state,message){
  const live=$('liveFeed'+channel);
  const snapshot=$('snapshotFeed'+channel);
  const empty=$('feedEmpty'+channel);
  const indicator=$('feedLive'+channel);
  const badge=$('feedBadge'+channel);
  const text=$('feedMessage'+channel);

  if(state==='loading'){
    live.hidden=false;
    snapshot.hidden=true;
    empty.hidden=true;
    indicator.hidden=true;
    badge.textContent='Abrindo';
    badge.className='badge warning';
  }else if(state==='live'){
    live.hidden=false;
    snapshot.hidden=true;
    empty.hidden=true;
    indicator.hidden=false;
    badge.textContent='Ao vivo';
    badge.className='badge success';
  }else if(state==='snapshot'){
    live.hidden=true;
    snapshot.hidden=false;
    empty.hidden=true;
    indicator.hidden=true;
    badge.textContent='Snapshot';
    badge.className='badge neutral';
  }else if(state==='error'){
    live.hidden=true;
    snapshot.hidden=!snapshotUrls[channel];
    empty.hidden=!!snapshotUrls[channel];
    indicator.hidden=true;
    badge.textContent='Falha';
    badge.className='badge danger';
  }else{
    live.hidden=true;
    snapshot.hidden=!snapshotUrls[channel];
    empty.hidden=!!snapshotUrls[channel];
    indicator.hidden=true;
    badge.textContent=snapshotUrls[channel]?'Snapshot':'Parado';
    badge.className='badge neutral';
  }

  if(message)text.textContent=message;
  if(lastData)enableControls(lastData);
}

async function loadSnapshotChannel(channel){
  if(liveChannels.size||pendingChannels.size)return false;
  const stream='ch'+String(channel).padStart(2,'0')+'_0';
  setFeedUi(channel,'loading','Capturando um frame do stream principal...');
  try{
    const response=await fetch(api('api/snapshot/'+stream+'?t='+Date.now()),{cache:'no-store'});
    if(!response.ok)throw new Error('HTTP '+response.status);
    const blob=await response.blob();
    const objectUrl=URL.createObjectURL(blob);
    const old=snapshotUrls[channel];
    snapshotUrls[channel]=objectUrl;
    const image=$('snapshotFeed'+channel);
    image.src=objectUrl;
    if(old)URL.revokeObjectURL(old);
    setFeedUi(channel,'snapshot',response.headers.get('X-JOOAN-Snapshot')==='stale'?'Snapshot em cache temporário.':'Snapshot atualizado.');
    return true;
  }catch(error){
    setFeedUi(channel,'error','Falha no snapshot: '+error.message);
    return false;
  }
}

async function refreshSnapshots(){
  if(!lastData?.online||!lastData?.authenticated||liveChannels.size||pendingChannels.size)return;
  $('previewMessage').textContent='Atualizando as duas imagens, uma de cada vez...';
  await loadSnapshotChannel(0);
  await sleep(500);
  await loadSnapshotChannel(1);
  $('previewMessage').textContent='Snapshots concluídos. Nenhuma sessão RTSP contínua permanece aberta.';
}

function waitForImageOutcome(image,timeoutMs=12500){
  return new Promise(resolve=>{
    let done=false;
    const finish=value=>{
      if(done)return;
      done=true;
      clearTimeout(timer);
      image.removeEventListener('load',onLoad);
      image.removeEventListener('error',onError);
      resolve(value);
    };
    const onLoad=()=>finish(true);
    const onError=()=>finish(false);
    image.addEventListener('load',onLoad,{once:true});
    image.addEventListener('error',onError,{once:true});
    const timer=setTimeout(()=>finish(false),timeoutMs);
  });
}

async function startFeed(channel,{waitReady=false}={}){
  if(
    !lastData?.online||
    !lastData?.authenticated||
    liveChannels.has(channel)||
    pendingChannels.has(channel)
  )return false;

  const image=$('liveFeed'+channel);
  pendingChannels.add(channel);
  setFeedUi(channel,'loading','Abrindo RTSP autenticado no backend...');
  if(lastData)setHealth(lastData);

  const outcome=waitReady?waitForImageOutcome(image):null;
  image.src=api('api/live/'+channel+'?t='+Date.now());

  if(!waitReady)return true;

  const opened=await outcome;
  if(!opened){
    pendingChannels.delete(channel);
    liveChannels.delete(channel);
    image.removeAttribute('src');
    try{await fetch(api('api/live/stop?channel='+channel),{method:'POST',keepalive:true})}catch(_){}
    setFeedUi(channel,'error','O feed não entregou imagem dentro do tempo esperado.');
    if(lastData){
      setHealth(lastData);
      enableControls(lastData);
    }
  }
  return opened;
}

async function stopFeed(channel){
  const image=$('liveFeed'+channel);
  image.removeAttribute('src');
  pendingChannels.delete(channel);
  liveChannels.delete(channel);
  try{
    await fetch(api('api/live/stop?channel='+channel),{method:'POST',keepalive:true});
  }catch(_){}
  setFeedUi(channel,'stopped','Feed parado. Nenhuma sessão contínua deste canal permanece aberta.');
  if(lastData)renderAll(lastData);
}

async function startAllFeeds(){
  if(!lastData?.online||!lastData?.authenticated)return;
  $('previewMessage').textContent='Abrindo primeiro a lente 1 para evitar duas negociações RTSP simultâneas...';
  const first=await startFeed(0,{waitReady:true});
  if(!first){
    $('previewMessage').textContent='A lente 1 não abriu; a lente 2 não será iniciada automaticamente para poupar a câmera.';
    return;
  }
  await sleep(500);
  $('previewMessage').textContent='Lente 1 ativa. Abrindo lente 2...';
  const second=await startFeed(1,{waitReady:true});
  $('previewMessage').textContent=second?'Os dois feeds estão ativos.':'Lente 1 ativa; lente 2 falhou ao iniciar.';
}

async function stopAllFeeds(){
  for(const channel of [0,1]){
    $('liveFeed'+channel).removeAttribute('src');
  }
  pendingChannels.clear();
  liveChannels.clear();
  try{await fetch(api('api/live/stop'),{method:'POST',keepalive:true})}catch(_){}
  for(const channel of [0,1])setFeedUi(channel,'stopped','Feed parado.');
  $('previewMessage').textContent='Todos os feeds foram encerrados.';
  if(lastData)renderAll(lastData);
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
  while(activeDirection===direction&&generation===ptzHoldGeneration&&result?.transport==='onvif'){
    await sleep(35);
    if(activeDirection!==direction||generation!==ptzHoldGeneration)break;
    result=await sendPtz(direction);
  }
}

function emergencyStop(){
  if(!activeDirection)return;
  activeDirection=null;
  ptzHoldGeneration++;
  void sendPtz('stop',true);
}

for(const channel of [0,1]){
  const live=$('liveFeed'+channel);
  live.addEventListener('load',()=>{
    pendingChannels.delete(channel);
    liveChannels.add(channel);
    setFeedUi(channel,'live','Feed contínuo recebido pelo navegador.');
    if(lastData)renderAll(lastData);
  });
  live.addEventListener('error',()=>{
    pendingChannels.delete(channel);
    liveChannels.delete(channel);
    setFeedUi(channel,'error','O backend não conseguiu manter este feed.');
    if(lastData)renderAll(lastData);
  });
}

document.querySelectorAll('[data-snapshot-channel]').forEach(button=>{
  button.addEventListener('click',()=>loadSnapshotChannel(Number(button.dataset.snapshotChannel)));
});
document.querySelectorAll('[data-live-channel]').forEach(button=>{
  button.addEventListener('click',()=>startFeed(Number(button.dataset.liveChannel)));
});
document.querySelectorAll('[data-stop-channel]').forEach(button=>{
  button.addEventListener('click',()=>stopFeed(Number(button.dataset.stopChannel)));
});

document.querySelectorAll('[data-dir]').forEach(button=>{
  const direction=button.dataset.dir;
  button.addEventListener('pointerdown',event=>{
    event.preventDefault();
    activeDirection=direction;
    const generation=++ptzHoldGeneration;
    button.setPointerCapture?.(event.pointerId);
    void holdPtz(direction,generation);
  });
  const stop=()=>{
    if(activeDirection===direction){
      activeDirection=null;
      ptzHoldGeneration++;
      void sendPtz('stop');
    }
  };
  button.addEventListener('pointerup',stop);
  button.addEventListener('pointercancel',stop);
  button.addEventListener('lostpointercapture',stop);
});

$('stop').addEventListener('click',()=>{
  activeDirection=null;
  ptzHoldGeneration++;
  void sendPtz('stop');
});
$('refreshSnapshots').addEventListener('click',refreshSnapshots);
$('startLive').addEventListener('click',startAllFeeds);
$('stopLive').addEventListener('click',stopAllFeeds);

window.addEventListener('blur',emergencyStop);
document.addEventListener('visibilitychange',()=>{
  if(document.hidden){
    emergencyStop();
    if(liveChannels.size||pendingChannels.size)void stopAllFeeds();
  }
});
window.addEventListener('pagehide',()=>{
  emergencyStop();
  if(liveChannels.size||pendingChannels.size)void stopAllFeeds();
  for(const url of Object.values(snapshotUrls))if(url)URL.revokeObjectURL(url);
});

void refreshStatus();
statusTimer=setInterval(()=>{if(!document.hidden)void refreshStatus()},STATUS_REFRESH_MS);

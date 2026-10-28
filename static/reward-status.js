(()=>{'use strict';let state=null,offset=0;window.rewardClosed=true;
const notice=document.createElement('div');notice.className='notice';notice.setAttribute('role','status');notice.style.margin='14px 20px';notice.textContent='Bestellzeiten werden geprüft …';document.querySelector('.header')?.after(notice);
function show(){if(!state)return;window.rewardClosed=!state.offen;notice.hidden=state.offen;
if(state.offen)return;if(!state.naechste_oeffnung){notice.textContent='Zurzeit sind keine Bestellzeiten freigegeben.';return;}
const next=new Date(state.naechste_oeffnung),seconds=Math.max(0,Math.ceil((next.getTime()-Date.now()-offset)/1000));
const when=new Intl.DateTimeFormat('de-DE',{weekday:'long',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Berlin'}).format(next);
notice.textContent=(state.ruhetag?'Heute ist Ruhetag. ':'Noch nicht für Bestellungen verfügbar. ')+'Wieder ab '+when+' Uhr. Verbleibend: '+Math.floor(seconds/3600)+' Std. '+Math.floor(seconds%3600/60)+' Min.';}
async function update(){try{const response=await fetch('/api/bestellstatus',{cache:'no-store'});if(!response.ok)throw Error();state=await response.json();offset=new Date(state.server_zeit).getTime()-Date.now();show();}catch{window.rewardClosed=true;notice.hidden=false;notice.textContent='Bestellzeiten konnten nicht geladen werden. Bitte Verbindung prüfen.';}}
update();setInterval(update,30000);setInterval(show,1000);})();

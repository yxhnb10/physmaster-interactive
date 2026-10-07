/* PHY_THEME_V962: local appearance only, never calls an API. */
(()=>{'use strict';const state=window.PhysMasterAppearance;if(!state||document.getElementById('phyThemeSelect'))return;
const wrapper=document.createElement('label');wrapper.className='phy-theme-control';wrapper.htmlFor='phyThemeSelect';wrapper.append(document.createTextNode('外观'));
const select=document.createElement('select');select.id='phyThemeSelect';select.setAttribute('aria-label','网页外观');select.title='此浏览器记住外观选择；跟随系统会自动响应系统主题变化。';
for(const [value,text] of [['system','跟随系统'],['light','浅色'],['dark','深色']]){const option=document.createElement('option');option.value=value;option.textContent=text;select.append(option);}wrapper.append(select);
const team=document.getElementById('teamBar');if(team)team.append(wrapper);else{wrapper.classList.add('phy-theme-login');document.querySelector('main')?.prepend(wrapper);}
const notice=document.createElement('span');notice.className='phy-theme-sr';notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');wrapper.append(notice);
function sync(){select.value=state.getMode();notice.textContent='当前网页使用'+(document.documentElement.dataset.phyTheme==='dark'?'深色':'浅色')+'外观';}
select.addEventListener('change',()=>{state.apply(select.value);sync();try{localStorage.setItem(state.key,state.getMode());select.title='外观选择已保存到此浏览器。';}catch{select.title='浏览器阻止保存；本页外观已切换，刷新后可能恢复默认。';notice.textContent+='；浏览器阻止保存选择';}});
const systemChanged=()=>{if(state.getMode()==='system'){state.apply('system');sync();}};
if(state.media?.addEventListener)state.media.addEventListener('change',systemChanged);else state.media?.addListener?.(systemChanged);
window.addEventListener('storage',event=>{if(event.storageArea&&event.storageArea!==localStorage)return;if(event.key===state.key||event.key===null){state.apply(['system','light','dark'].includes(event.newValue)?event.newValue:'system');sync();}});
sync();const footer=document.querySelector('footer');if(footer)footer.textContent=footer.textContent.replace('v9.6.1','v9.6.2');
})();

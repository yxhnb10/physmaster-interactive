/* PHY_THEME_V962: run before styles to avoid a bright flash on dark reload. */
(()=>{'use strict';
const key='physmaster-appearance-v1';let mode='system';
try{const saved=localStorage.getItem(key);if(['system','light','dark'].includes(saved))mode=saved;}catch{}
let media=null;try{media=typeof matchMedia==='function'?matchMedia('(prefers-color-scheme: dark)'):null;}catch{}
const root=document.documentElement;
function apply(value){mode=['system','light','dark'].includes(value)?value:'system';const actual=mode==='system'?(media?.matches?'dark':'light'):mode;root.dataset.phyTheme=actual;root.dataset.phyThemeMode=mode;root.style.colorScheme=actual;return actual;}
apply(mode);
window.PhysMasterAppearance={version:'v9.6.2-dark-mode-1',key,media,apply,getMode:()=>mode};
})();

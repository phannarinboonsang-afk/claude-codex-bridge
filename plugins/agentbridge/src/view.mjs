import {App} from '@modelcontextprotocol/ext-apps';
import {RunView} from './control.mjs';
const app=new App({name:'AgentBridge',version:'1.0.0'},{});
const view=new RunView(document,(name,args)=>app.callServerTool({name,arguments:args}));
app.ontoolresult=result=>view.receive(result.structuredContent);
window.addEventListener('pagehide',()=>view.dispose());
await app.connect();

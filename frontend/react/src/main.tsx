import React,{useState} from "react";
import {createRoot} from "react-dom/client";
import {BrowserRouter,Link,Route,Routes} from "react-router-dom";
import {QueryClient,QueryClientProvider} from "@tanstack/react-query";
import {Login} from "./pages/Login";
import {Dashboard} from "./pages/Dashboard";
import {Create} from "./pages/Create";
import {Workspace} from "./pages/Workspace";
import "leaflet/dist/leaflet.css";
import "./style.css";
const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
function App(){const [logged,L]=useState(false);return <QueryClientProvider client={client}>{!logged?<Login done={()=>L(true)}/>:<BrowserRouter><div className="layout"><aside><div className="brand">◈ <span>Investigation<br/>Network</span></div><nav><Link to="/">Overview</Link><Link to="/create">Create case</Link></nav></aside><main><header><span>COMMAND CENTER</span><span>Evidence review</span></header><Routes><Route path="/" element={<Dashboard/>}/><Route path="/create" element={<Create/>}/><Route path="/cases/:id" element={<Workspace/>}/></Routes></main></div></BrowserRouter>}</QueryClientProvider>}
createRoot(document.getElementById("root")!).render(<App/>);

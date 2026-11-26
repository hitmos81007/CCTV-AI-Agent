import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, NavLink, Route, Routes, useParams } from "react-router-dom";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import "leaflet/dist/leaflet.css";
import "./style.css";

const BASE = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
type Case = {case_id:string; description:string; initial_zone:string; neighbors:Record<string,string[]>; clues:unknown};
type Camera = {camera_id:string; name:string; jurisdiction:string};
type Recording = {id:string; camera_id:string; jurisdiction:string; source_type:string; status:string; recorded_at:string|null};
type Sighting = {id:string; camera_id:string; jurisdiction:string; occurred_at:string; vehicle_type:string; detector_confidence:number; review_status:string; frame_url:string|null};
async function api<T>(path:string):Promise<T> {
  let response:Response;
  try { response=await fetch(BASE+path,{credentials:"include"}); }
  catch { throw new Error("Cannot reach the investigation backend. Check that it is running and allows this site."); }
  if(!response.ok) throw new Error(response.status===401?"Authentication required. Open the backend and sign in, then retry.":`Backend request failed (${response.status}).`);
  return response.json() as Promise<T>;
}
function Data<T>({query, children}:{query:ReturnType<typeof useQuery<T,Error>>;children:(value:T)=>React.ReactNode}) {
  if(query.isLoading)return <p role="status">Loading…</p>;
  if(query.error)return <p role="alert" className="error">{query.error.message}</p>;
  if(!query.data)return <p>No data available.</p>;
  return <>{children(query.data)}</>;
}
function Dashboard(){
  const cases=useQuery<Case[],Error>({queryKey:["cases"],queryFn:()=>api("/api/v1/cases")});
  const cameras=useQuery<Camera[],Error>({queryKey:["cameras"],queryFn:()=>api("/api/v1/cameras")});
  return <><h1>Investigation overview</h1><p className="muted">Cases and camera sources from your connected backend.</p><div className="metrics"><article><span>Investigations</span><strong>{cases.data?.length ?? "—"}</strong></article><article><span>Registered cameras</span><strong>{cameras.data?.length ?? "—"}</strong></article></div><section><h2>Recent investigations</h2><Data query={cases}>{rows=>rows.length?<div className="list">{rows.map(c=><Link className="row" key={c.case_id} to={"/cases/"+encodeURIComponent(c.case_id)}><strong>{c.case_id}</strong><span>{c.description}</span><span>{Object.keys(c.neighbors||{}).length} jurisdictions</span></Link>)}</div>:<p>No investigations yet.</p>}</Data></section><section><h2>Camera registry</h2><Data query={cameras}>{rows=>rows.length?<div className="list">{rows.map(c=><div className="row" key={c.camera_id}><strong>{c.name}</strong><span>{c.camera_id}</span><span>{c.jurisdiction}</span></div>)}</div>:<p>No cameras registered.</p>}</Data></section></>;
}
function Workspace(){
  const {id=""}=useParams();
  const encoded=encodeURIComponent(id);
  const item=useQuery<Case,Error>({queryKey:["case",id],queryFn:()=>api("/api/v1/cases/"+encoded)});
  const records=useQuery<{items:Recording[]},Error>({queryKey:["recordings",id],queryFn:()=>api("/api/v1/recordings?case_id="+encoded)});
  const sightings=useQuery<Sighting[],Error>({queryKey:["sightings",id],queryFn:()=>api("/api/v1/sightings?case_id="+encoded)});
  return <><Data query={item}>{c=><><h1>{c.case_id}</h1><p className="muted">{c.description}</p><section><h2>Jurisdictions</h2><div className="chips">{Object.entries(c.neighbors||{}).map(([name,neighbors])=><div key={name}><strong>{name}</strong><small>Neighbors: {neighbors.join(", ")||"none configured"}</small></div>)}</div></section><section><h2>Parsed clue</h2><pre>{c.clues?JSON.stringify(c.clues,null,2):"No parsed clue is stored."}</pre></section></>}</Data><section><h2>Recording library</h2><Data query={records}>{r=>r.items.length?<div className="list">{r.items.map(x=><div className="row" key={x.id}><strong>{x.camera_id}</strong><span>{x.jurisdiction} · {x.source_type}</span><span>{x.status} · {x.recorded_at||"time unavailable"}</span></div>)}</div>:<p>No recordings registered.</p>}</Data></section><section><h2>Evidence candidates</h2><Data query={sightings}>{rows=>rows.length?<div className="cards">{rows.map(s=><article key={s.id}>{s.frame_url&&<img crossOrigin="use-credentials" src={BASE+s.frame_url} alt={`Evidence from camera ${s.camera_id}`}/>}<strong>{s.vehicle_type} · {s.jurisdiction}</strong><p>{s.camera_id} · {s.occurred_at}</p><p>Detector confidence {s.detector_confidence.toFixed(2)} · {s.review_status}</p></article>)}</div>:<p>No evidence candidates found.</p>}</Data></section></>;
}
function App(){
 const [query] = useState(()=>new QueryClient());
 return <QueryClientProvider client={query}><BrowserRouter><div className="layout"><aside><div className="brand">◈ <span>Investigation<br/>Network</span></div><nav><NavLink to="/">Overview</NavLink></nav><p className="aside-note">Connected investigation workspace</p></aside><main><header><span>COMMAND CENTER</span><span>Evidence review</span></header><Routes><Route path="/" element={<Dashboard/>}/><Route path="/cases/:id" element={<Workspace/>}/></Routes></main></div></BrowserRouter></QueryClientProvider>;
}
createRoot(document.getElementById("root")!).render(<App/>);

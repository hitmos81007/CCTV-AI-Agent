import React from "react";
import {Link} from "react-router-dom";
import {useQueries,useQuery} from "@tanstack/react-query";
import {request} from "../api";
import type {Case,Camera,Progress} from "../types";
import {ErrorBox} from "../components/Common";

export function Dashboard(){
  const cases=useQuery({queryKey:["cases"],queryFn:()=>request<Case[]>("/api/v1/cases")});
  const cameras=useQuery({queryKey:["cameras"],queryFn:()=>request<Camera[]>("/api/v1/cameras")});
  const health=useQuery({queryKey:["health"],queryFn:()=>request<{status:string;ollama_ready:boolean}>("/health"),retry:false});
  const progress=useQueries({queries:(cases.data||[]).map(c=>({queryKey:["progress",c.case_id],queryFn:()=>request<Progress>("/api/v1/cases/"+encodeURIComponent(c.case_id)+"/progress")}))});
  const loading=progress.some(q=>q.isLoading), failed=progress.some(q=>q.isError);
  const jobs=progress.flatMap(q=>q.data?.jobs||[]);
  const evidence=progress.reduce((sum,q)=>sum+(q.data?.sighting_count||0),0);
  const jurisdictions=new Set((cases.data||[]).flatMap(c=>Object.keys(c.neighbors||{})));
  const metric=(value:number)=>loading||failed?"—":String(value);
  return <><h1>Investigation overview</h1><p className="muted">Live case, camera and processing information.</p>
    <div className="metrics">
      <article><span>Investigations</span><strong>{cases.data?.length??"—"}</strong></article>
      <article><span>Cameras</span><strong>{cameras.data?.length??"—"}</strong></article>
      <article><span>Jurisdictions</span><strong>{cases.data?jurisdictions.size:"—"}</strong></article>
      <article><span>Evidence records</span><strong>{metric(evidence)}</strong></article>
      <article><span>Queued jobs</span><strong>{metric(jobs.filter(j=>["queued","retrying"].includes(j.status)).length)}</strong></article>
      <article><span>Running jobs</span><strong>{metric(jobs.filter(j=>["running","processing"].includes(j.status)).length)}</strong></article>
      <article><span>Completed jobs</span><strong>{metric(jobs.filter(j=>j.status==="completed").length)}</strong></article>
    </div>
    <section><h2>Services</h2>{health.isLoading?<p>Checking backend…</p>:health.error?<ErrorBox error={health.error}/>:<p>Backend: {health.data?.status||"unknown"} · Ollama: {health.data?.ollama_ready?"available":"unavailable"}</p>}</section>
    <section><h2>Recent investigations</h2><Link to="/create">Create investigation</Link>{cases.isLoading&&<p>Loading cases…</p>}<ErrorBox error={cases.error}/><ErrorBox error={cameras.error}/>{failed&&<p className="error">Some progress metrics are unavailable.</p>}{cases.data?.length===0&&<p>No cases yet.</p>}
      {cases.data?.map(c=><Link className="row" key={c.case_id} to={"/cases/"+encodeURIComponent(c.case_id)}><strong>{c.case_id}</strong><span>{c.description}</span><span>{Object.keys(c.neighbors||{}).length} jurisdictions</span></Link>)}
    </section>
  </>;
}

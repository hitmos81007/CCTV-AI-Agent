import React, {useState} from "react";
import {useNavigate} from "react-router-dom";
import {useQueryClient} from "@tanstack/react-query";
import {request} from "../api";
import {ErrorBox,post} from "../components/Common";

export function Create(){
  const nav=useNavigate(), cache=useQueryClient();
  const [id,setId]=useState(""),[description,setDescription]=useState(""),[zone,setZone]=useState("Zone-1");
  const [vehicle,setVehicle]=useState(""),[color,setColor]=useState(""),[registration,setRegistration]=useState("");
  const [location,setLocation]=useState(""),[when,setWhen]=useState(""),[timezone,setTimezone]=useState(Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [map,setMap]=useState('{"Zone-1":["Zone-2"],"Zone-2":["Zone-1"],"Zone-3":[],"Zone-4":[],"Zone-5":[],"Zone-6":[]}');
  const [error,setError]=useState(""),[saving,setSaving]=useState(false);
  async function submit(event:React.FormEvent){
    event.preventDefault();setError("");setSaving(true);
    try{
      const neighbors=JSON.parse(map);
      const pieces=[description.trim(),vehicle&&"Vehicle type: "+vehicle,color&&"Color: "+color,
        registration&&"Registration clue: "+registration,location&&"Location: "+location,
        when&&"Incident time: "+when+" ("+timezone+")"].filter(Boolean);
      await request("/api/v1/cases",post({case_id:id.trim(),description:pieces.join(". "),initial_zone:zone.trim(),neighbors}));
      await cache.invalidateQueries({queryKey:["cases"]});
      nav("/cases/"+encodeURIComponent(id.trim()));
    }catch(e){setError(e instanceof Error?e.message:"Unable to create case")}finally{setSaving(false)}
  }
  return <><h1>Create investigation</h1><p className="muted">Enter the information available to the investigator.</p><section><form onSubmit={submit}>
    <label>Case ID<input value={id} onChange={e=>setId(e.target.value)} maxLength={80} required/></label>
    <label>Incident description<textarea value={description} onChange={e=>setDescription(e.target.value)} required minLength={3}/></label>
    <div className="form-grid"><label>Vehicle type<input value={vehicle} onChange={e=>setVehicle(e.target.value)}/></label><label>Vehicle color<input value={color} onChange={e=>setColor(e.target.value)}/></label><label>Registration clue<input value={registration} onChange={e=>setRegistration(e.target.value)}/></label></div>
    <div className="form-grid"><label>Incident location<input value={location} onChange={e=>setLocation(e.target.value)}/></label><label>Incident date and time<input type="datetime-local" value={when} onChange={e=>setWhen(e.target.value)}/></label><label>Time zone<input value={timezone} onChange={e=>setTimezone(e.target.value)}/></label></div>
    <label>Initial jurisdiction<input value={zone} onChange={e=>setZone(e.target.value)} required/></label>
    <label>Jurisdictions and neighbors (JSON)<textarea rows={5} value={map} onChange={e=>setMap(e.target.value)}/></label>
    <button disabled={saving}>{saving?"Creating…":"Create case"}</button><ErrorBox error={error}/>
  </form></section></>;
}

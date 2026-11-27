import React from "react";
import { useParams } from "react-router-dom";
import { InvestigationWorkspace } from "../components/InvestigationWorkspace";

export function Workspace() {
  const { id = "" } = useParams();
  return <InvestigationWorkspace initialCaseId={id} />;
}

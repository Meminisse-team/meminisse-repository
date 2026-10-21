import { useCallback, useReducer } from "react";
export type AutobiographyJob = "idle" | "consolidating" | "writing" | "finalizing" | "generating_pdf";
type Action = { type: "start" | "finish"; job: Exclude<AutobiographyJob, "idle"> };
export function jobReducer(state: AutobiographyJob, action: Action): AutobiographyJob {
  if (action.type === "start") return state === "idle" ? action.job : state;
  return state === action.job ? "idle" : state;
}
export function useAutobiographyJob() {
  const [job, dispatch] = useReducer(jobReducer, "idle");
  const set = useCallback((target: Exclude<AutobiographyJob, "idle">, active: boolean) => dispatch({ type: active ? "start" : "finish", job: target }), []);
  const setConsolidateTriggered = useCallback((active: boolean) => set("consolidating", active), [set]);
  const setChaptersWriteTriggered = useCallback((active: boolean) => set("writing", active), [set]);
  const setFinalizeTriggered = useCallback((active: boolean) => set("finalizing", active), [set]);
  const setPdfTriggered = useCallback((active: boolean) => set("generating_pdf", active), [set]);
  return { job, consolidateTriggered: job === "consolidating", chaptersWriteTriggered: job === "writing", finalizeTriggered: job === "finalizing", pdfTriggered: job === "generating_pdf", setConsolidateTriggered, setChaptersWriteTriggered, setFinalizeTriggered, setPdfTriggered };
}

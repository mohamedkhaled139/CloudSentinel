import { useOutletContext } from "react-router-dom";
import type { ShellContext } from "../components/Layout";

export function useShell(): ShellContext {
  return useOutletContext<ShellContext>() ?? { setOpen: () => undefined, path: "/" };
}

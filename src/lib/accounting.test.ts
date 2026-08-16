import {describe,expect,it} from "vitest";
import {advancedComposition,sequentialComposition} from "./accounting";
describe("privacy accounting",()=>{it("composes epsilon sequentially",()=>expect(sequentialComposition([.2,.3,.5])).toBe(1));it("reports finite advanced composition",()=>expect(Number.isFinite(advancedComposition([.1,.1],1e-6))).toBe(true));it("handles no releases",()=>expect(advancedComposition([])).toBe(0));});

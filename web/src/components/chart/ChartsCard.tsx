/** Numbered editorial sections 02 Spectrum and 03 Light curve. */
import type { Meta } from "../../types";
import { SectionHead } from "../Progress";
import { Segmented } from "../ui/primitives";
import { LightCurveChart } from "./LightCurveChart";
import { SpectrumChart } from "./SpectrumChart";

export function ChartSections({ meta, epoch, ylog, onYlog }: { meta: Meta; epoch: number; ylog: boolean; onYlog: (v: boolean) => void }) {
  return (
    <div className="cell-charts">
      <section className="cell" aria-label="Spectrum">
        <SectionHead n="02" title="Spectrum">
          <Segmented<"linear" | "log">
            label="Flux scale"
            size="sm"
            value={ylog ? "log" : "linear"}
            onChange={(v) => onYlog(v === "log")}
            options={[
              { value: "linear", label: "Lin" },
              { value: "log", label: "Log" },
            ]}
          />
        </SectionHead>
        <div className="cell-body">
          <SpectrumChart meta={meta} epoch={epoch} ylog={ylog} />
        </div>
      </section>
      <section className="cell" aria-label="Light curve">
        <SectionHead n="03" title="Light curve" />
        <div className="cell-body">
          <LightCurveChart meta={meta} epoch={epoch} />
        </div>
      </section>
    </div>
  );
}

export function ChartSkeletons() {
  return (
    <div className="cell-charts" aria-hidden>
      {["02", "03"].map((n) => (
        <section key={n} className="cell">
          <SectionHead n={n} title={n === "02" ? "Spectrum" : "Light curve"} />
          <div className="cell-body">
            <div className="skeleton" style={{ height: 300 }} />
          </div>
        </section>
      ))}
    </div>
  );
}

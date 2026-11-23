import { useState } from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { cleanValues, defaultsFor, SchemaForm } from "../components/SchemaForm";
import { StatusBadge, SyntheticBadge } from "../components/ui";
import type { JsonSchema } from "../api/types";

const schema: JsonSchema = {
  type: "object",
  required: ["url"],
  properties: {
    url: { type: "string", title: "Url", description: "Endpoint to probe" },
    method: { enum: ["GET", "HEAD"], type: "string", default: "GET", title: "Method" },
    verify_tls: { type: "boolean", default: true, title: "Verify Tls" },
    expected_status_codes: { type: "array", items: { type: "integer" }, title: "Expected Status Codes" },
    body_contains: { anyOf: [{ type: "string" }, { type: "null" }], default: null, title: "Body Contains" },
    tls_expiry_warning_days: { type: "integer", default: 14, title: "Tls Expiry Warning Days" },
  },
};

function Harness({ onValues }: { onValues: (v: Record<string, unknown>) => void }) {
  const [values, setValues] = useState<Record<string, unknown>>(defaultsFor(schema));
  return (
    <SchemaForm
      schema={schema}
      values={values}
      onChange={(v) => {
        setValues(v);
        onValues(v);
      }}
    />
  );
}

describe("SchemaForm", () => {
  it("renders fields from a JSON schema and produces typed values", async () => {
    let latest: Record<string, unknown> = {};
    render(<Harness onValues={(v) => (latest = v)} />);
    expect(screen.getByLabelText("Url *")).toBeInTheDocument();
    expect(screen.getByText("Endpoint to probe")).toBeInTheDocument();
    expect((screen.getByLabelText("Method") as HTMLSelectElement).value).toBe("GET");
    expect(screen.getByLabelText("Verify Tls")).toBeChecked();

    await userEvent.type(screen.getByLabelText("Url *"), "https://example.org/health");
    await userEvent.type(screen.getByLabelText("Expected Status Codes"), "200, 204");
    await userEvent.selectOptions(screen.getByLabelText("Method"), "HEAD");
    await userEvent.click(screen.getByLabelText("Verify Tls"));

    expect(latest.url).toBe("https://example.org/health");
    expect(latest.expected_status_codes).toEqual([200, 204]);
    expect(latest.method).toBe("HEAD");
    expect(latest.verify_tls).toBe(false);
    expect(latest.tls_expiry_warning_days).toBe(14);
  });

  it("drops empty optional values", () => {
    expect(cleanValues({ a: "", b: null, c: 0, d: false, e: "x" })).toEqual({ c: 0, d: false, e: "x" });
  });

  it("masks secret fields and indicates stored secrets", () => {
    render(
      <SchemaForm
        schema={{ properties: { bearer_token: { type: "string", title: "Bearer Token" } } }}
        values={{}}
        onChange={() => undefined}
        secret
        setFields={["bearer_token"]}
      />,
    );
    const input = screen.getByLabelText("Bearer Token") as HTMLInputElement;
    expect(input.type).toBe("password");
    expect(input.placeholder).toMatch(/stored/);
  });
});

describe("status presentation", () => {
  it("always renders a text label, not color alone", () => {
    render(
      <>
        <StatusBadge status="down" />
        <StatusBadge status="impacted" />
        <SyntheticBadge />
      </>,
    );
    expect(screen.getByText("Down")).toBeInTheDocument();
    expect(screen.getByText("Impacted")).toBeInTheDocument();
    expect(screen.getByText("Synthetic")).toBeInTheDocument();
  });
});

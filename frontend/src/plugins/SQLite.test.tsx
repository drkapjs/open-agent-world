// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { PluginViewProps } from "./sdk";
import { Database } from "../../../plugins/sqlite/frontend";

afterEach(cleanup);
const schema = { schema_version: 1, objects: [{ type: "table", name: "notes", sql: "CREATE TABLE notes(text TEXT)", table: "notes" }], truncated: false };
function setup() {
  const action = vi.fn(async (name: string, _args: Record<string, unknown>, confirm = false): Promise<Record<string, unknown>> => {
    if (name === "inspect") return schema;
    if (name === "admin" && !confirm) return { status: "confirmation_required", reasons: ["This statement can remove data"] };
    return { status: "ok", columns: ["text"], rows: [["saved"]], changes: confirm ? 1 : 0, elapsed_ms: 2 };
  });
  render(<Database {...{ card: { id: "sql-test", name: "Test" }, host: { resourceAction: action } } as unknown as PluginViewProps} />);
  return action;
}

it("queries through the scoped host and renders a table", async () => {
  const action = setup();
  await waitFor(() => expect((screen.getByRole("button", { name: "Run query" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText("SQL editor"), { target: { value: "SELECT text FROM notes" } });
  fireEvent.click(screen.getByRole("button", { name: "Run query" }));
  expect(await screen.findByText("saved")).toBeTruthy();
  expect(action).toHaveBeenCalledWith("query", { sql: "SELECT text FROM notes", parameters: [], max_rows: 200 }, false);
});

it("confirms the exact SQL and invalidates confirmation on edit", async () => {
  const action = setup();
  await screen.findByRole("option", { name: "notes · table" });
  fireEvent.click(screen.getByLabelText("Read only"));
  fireEvent.change(screen.getByLabelText("SQL editor"), { target: { value: "DELETE FROM notes" } });
  fireEvent.click(screen.getByRole("button", { name: "Run SQL" }));
  await screen.findByRole("button", { name: "Confirm and run" });
  fireEvent.change(screen.getByLabelText("SQL editor"), { target: { value: "DELETE FROM notes WHERE text='old'" } });
  expect(screen.queryByRole("button", { name: "Confirm and run" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Run SQL" }));
  fireEvent.click(await screen.findByRole("button", { name: "Confirm and run" }));
  await screen.findByText("saved");
  expect(action).toHaveBeenLastCalledWith("inspect", {});
  expect(action).toHaveBeenCalledWith("admin", { sql: "DELETE FROM notes WHERE text='old'", parameters: [], max_rows: 200, schema_version: 1 }, true);
});

it("reports malformed parameters without issuing SQL", async () => {
  const action = setup();
  await screen.findByRole("option", { name: "notes · table" });
  fireEvent.change(screen.getByLabelText("SQL parameters"), { target: { value: "null" } });
  fireEvent.click(screen.getByRole("button", { name: "Run query" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Parameters must be a JSON array or object");
  expect(action.mock.calls.every(([name]) => name === "inspect")).toBe(true);
});

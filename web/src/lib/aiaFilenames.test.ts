import { describe, expect, it } from "vitest"

import {
  applyCollisionSuffixes,
  buildPushFileName,
  numberedDisplayNames,
  planFilenames,
  sanitizeDisplayName,
  sanitizeSourceStem,
} from "@/lib/aiaFilenames"

describe("aiaFilenames (parity with pdfsplit.aia.filenames)", () => {
  it("sanitizes display names like Python", () => {
    expect(sanitizeDisplayName("Freshworks  Sept")).toBe("Freshworks-Sept")
    expect(sanitizeDisplayName("a/b\\c")).toBe("a-b-c")
    expect(sanitizeDisplayName("  Invoice 03  ")).toBe("Invoice-03")
  })

  it("builds packet-VEN3-number", () => {
    expect(
      buildPushFileName({
        sourceFilename: "march-bills.pdf",
        vendor: "GROCERYGRID PRIVATE LIMITED",
        documentNumber: "GGPL/4045/26-27",
      })
    ).toBe("march-bills-GRO-GGPL-4045-26-27.pdf")
  })

  it("vendor code: NA when vendor empty, uses what exists when short", () => {
    expect(
      buildPushFileName({
        sourceFilename: "march-bills.pdf",
        documentNumber: "INV-1",
      })
    ).toBe("march-bills-NA-INV-1.pdf")
    expect(
      buildPushFileName({
        sourceFilename: "march-bills.pdf",
        vendor: "AB",
        documentNumber: "INV-1",
      })
    ).toBe("march-bills-AB-INV-1.pdf")
  })

  it("omits empty number slot", () => {
    expect(
      buildPushFileName({
        sourceFilename: "march-bills.pdf",
        vendor: "Freshworks",
      })
    ).toBe("march-bills-FRE.pdf")
  })

  it("applies collision suffixes in document order", () => {
    expect(
      planFilenames("march-bills", [
        { vendor: "Freshworks", number: "INV-1" },
        { vendor: "Freshworks", number: "INV-1" },
      ])
    ).toEqual([
      "march-bills-FRE-INV-1.pdf",
      "march-bills-FRE-INV-1-2.pdf",
    ])
  })

  it("sanitizes unicode quotes slashes emoji", () => {
    const name = buildPushFileName({
      sourceFilename: "pkt.pdf",
      vendor: 'Acme "Corp"/東京 🚀',
      documentNumber: "INV-1",
    })
    expect(name).not.toMatch(/[/\\"'🚀]/)
    expect(name.endsWith(".pdf")).toBe(true)
  })

  it("sanitizes packet stem live", () => {
    expect(sanitizeSourceStem("march bills.pdf")).toBe("march-bills")
    expect(sanitizeSourceStem("Acme/Aug 2026")).toBe("Aug-2026")
    expect(sanitizeSourceStem("Acme Aug 2026")).toBe("Acme-Aug-2026")
  })

  it("numbers a bulk base for the vendor slot", () => {
    expect(numberedDisplayNames("Acme-Aug", 3)).toEqual([
      "Acme-Aug-01",
      "Acme-Aug-02",
      "Acme-Aug-03",
    ])
  })

  it("applyCollisionSuffixes is deterministic", () => {
    const bases = ["a__b__c.pdf", "a__b__c.pdf", "x.pdf"]
    expect(applyCollisionSuffixes(bases)).toEqual([
      "a__b__c.pdf",
      "a__b__c-2.pdf",
      "x.pdf",
    ])
  })
})

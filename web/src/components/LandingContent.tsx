import * as React from "react"
import { Button } from "@/components/ui/button"

export function LandingContent() {
  return (
    <div className="mt-16 pt-16 mb-24 border-t border-border">
      <div className="mx-auto max-w-4xl space-y-20">



        {/* How to split */}
        <section className="space-y-8">
          <h2 className="text-3xl font-display font-semibold text-foreground">
            How to split a PDF with multiple invoices
          </h2>
          <ol className="space-y-5 list-decimal list-inside text-muted-foreground text-[18px] leading-relaxed">
            <li><strong className="text-foreground">Upload your PDF.</strong> Upload the PDF containing multiple invoices. Each page is shown so you can review the file.</li>
            <li><strong className="text-foreground">Review the breaks.</strong> AI detects where one invoice ends and the next begins. Keep, remove or add breaks as needed.</li>
            <li><strong className="text-foreground">Download your invoices.</strong> Get one PDF for each invoice, packed into a single ZIP file.</li>
          </ol>
        </section>

        {/* What the AI Invoice Splitter does */}
        <section className="space-y-8">
          <h2 className="text-3xl font-display font-semibold text-foreground">
            What the AI Invoice Splitter does
          </h2>
          <div className="overflow-x-auto rounded-xl border border-border">
            <table className="w-full text-[16px] text-left">
              <thead className="bg-muted text-foreground border-b border-border">
                <tr>
                  <th className="px-6 py-4 font-semibold">Feature</th>
                  <th className="px-6 py-4 font-semibold">Detail</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border text-muted-foreground">
                <tr>
                  <td className="px-6 py-4 font-medium text-foreground">AI-suggested breaks</td>
                  <td className="px-6 py-4">Detects where one invoice ends and the next begins</td>
                </tr>
                <tr>
                  <td className="px-6 py-4 font-medium text-foreground">Manual control</td>
                  <td className="px-6 py-4">Keep, remove or add a break on any page</td>
                </tr>
                <tr>
                  <td className="px-6 py-4 font-medium text-foreground">Page preview</td>
                  <td className="px-6 py-4">Review every page before splitting</td>
                </tr>
                <tr>
                  <td className="px-6 py-4 font-medium text-foreground">Output</td>
                  <td className="px-6 py-4">One PDF per invoice in a single ZIP</td>
                </tr>
                <tr>
                  <td className="px-6 py-4 font-medium text-foreground">Price</td>
                  <td className="px-6 py-4">Free</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        {/* How accountants use it for Tally bookkeeping */}
        <section className="space-y-8">
          <h2 className="text-3xl font-display font-semibold text-foreground">
            How accountants use it for Tally bookkeeping
          </h2>
          <div className="space-y-6 text-muted-foreground text-[18px] leading-relaxed">
            <p>
              Accountants often receive a month's supplier invoices as one large scanned PDF. Instead of manually finding and separating each invoice, use the AI Invoice Splitter to turn the file into individual PDFs.
            </p>
            <p>
              You can then match each PDF to its purchase entry in Tally and keep the invoice as supporting documentation.
            </p>
          </div>
          <div className="overflow-x-auto rounded-xl border border-border">
            <table className="w-full text-[16px] text-left">
              <thead className="bg-muted text-foreground border-b border-border">
                <tr>
                  <th className="px-6 py-4 font-semibold w-20">Step</th>
                  <th className="px-6 py-4 font-semibold">What you do</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border text-muted-foreground">
                <tr><td className="px-6 py-4 text-foreground font-medium">1</td><td className="px-6 py-4">Collect the client's supplier invoices into one PDF</td></tr>
                <tr><td className="px-6 py-4 text-foreground font-medium">2</td><td className="px-6 py-4">Upload the PDF and split it into individual invoices</td></tr>
                <tr><td className="px-6 py-4 text-foreground font-medium">3</td><td className="px-6 py-4">Name each file using the vendor's name or invoice number</td></tr>
                <tr><td className="px-6 py-4 text-foreground font-medium">4</td><td className="px-6 py-4">Enter each invoice as a purchase voucher in Tally <span className="opacity-75">(link: how to enter purchase invoices in Tally)</span></td></tr>
                <tr><td className="px-6 py-4 text-foreground font-medium">5</td><td className="px-6 py-4">Keep the invoice PDF as the backup for that entry</td></tr>
              </tbody>
            </table>
          </div>
          <div className="space-y-6 text-muted-foreground text-[18px] leading-relaxed">
            <p>
              The splitter only separates PDFs. It does not read invoice data or create entries in Tally. To automatically read invoices and create Tally entries, see AI Accountant.
            </p>
            <p>
              Need a file you can import into Tally? See the PDF to Tally XML Converter.
            </p>
          </div>
        </section>

        {/* Why use an AI PDF splitter? */}
        <section className="space-y-8">
          <h2 className="text-3xl font-display font-semibold text-foreground">
            Why use an AI PDF splitter?
          </h2>
          <div className="space-y-6 text-muted-foreground text-[18px] leading-relaxed">
            <p>
              With a standard PDF splitter, you need to find the page numbers for each invoice yourself. This gets difficult when invoices have different page lengths.
            </p>
            <p>
              The AI Invoice Splitter detects where each invoice ends and the next one begins, so you can review the suggested breaks instead of finding them manually.
            </p>
          </div>
          <div className="overflow-x-auto rounded-xl border border-border">
            <table className="w-full text-[16px] text-left">
              <thead className="bg-muted text-foreground border-b border-border">
                <tr>
                  <th className="px-6 py-4 font-semibold w-1/4"></th>
                  <th className="px-6 py-4 font-semibold w-1/4">Generic PDF splitters</th>
                  <th className="px-6 py-4 font-semibold w-1/4">Enterprise document AI</th>
                  <th className="px-6 py-4 font-semibold w-1/4">AI Invoice Splitter</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border text-muted-foreground">
                <tr>
                  <td className="px-6 py-4 font-medium text-foreground">How breaks are set</td>
                  <td className="px-6 py-4">You enter page numbers, ranges or file size</td>
                  <td className="px-6 py-4">Uses document rules or trained models</td>
                  <td className="px-6 py-4 font-medium text-foreground">AI suggests the breaks; you confirm them</td>
                </tr>
                <tr>
                  <td className="px-6 py-4 font-medium text-foreground">Setup</td>
                  <td className="px-6 py-4">None</td>
                  <td className="px-6 py-4">Platform setup or model training</td>
                  <td className="px-6 py-4 font-medium text-foreground">None</td>
                </tr>
                <tr>
                  <td className="px-6 py-4 font-medium text-foreground">Reads invoice data</td>
                  <td className="px-6 py-4">No</td>
                  <td className="px-6 py-4">Yes</td>
                  <td className="px-6 py-4 font-medium text-foreground">No, splitting only</td>
                </tr>
                <tr>
                  <td className="px-6 py-4 font-medium text-foreground">Output</td>
                  <td className="px-6 py-4">Files based on your selected ranges</td>
                  <td className="px-6 py-4">Extracted data or documents</td>
                  <td className="px-6 py-4 font-medium text-foreground">One PDF per invoice in a ZIP</td>
                </tr>
                <tr>
                  <td className="px-6 py-4 font-medium text-foreground">Cost</td>
                  <td className="px-6 py-4">Varies</td>
                  <td className="px-6 py-4">Varies</td>
                  <td className="px-6 py-4 font-medium text-foreground">Free</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        {/* Split, unmerge or separate any PDF */}
        <section className="space-y-8">
          <h2 className="text-3xl font-display font-semibold text-foreground">
            Split, unmerge or separate any PDF
          </h2>
          <p className="text-muted-foreground text-[18px] leading-relaxed">
            The tool isn't limited to invoices. Add breaks wherever you need them to split a combined PDF into separate documents.
          </p>
          <p className="text-foreground font-medium text-[18px]">Use it to:</p>
          <ul className="list-disc list-inside space-y-3 text-muted-foreground text-[18px] leading-relaxed">
            <li>Split a multi-invoice PDF</li>
            <li>Unmerge a combined PDF</li>
            <li>Separate documents from a scanned file</li>
            <li>Divide one PDF into multiple files</li>
            <li>Split every page into a separate PDF</li>
          </ul>
        </section>

        {/* Who uses it */}
        <section className="space-y-8">
          <h2 className="text-3xl font-display font-semibold text-foreground">
            Who uses it
          </h2>
          <ul className="list-disc list-inside space-y-3 text-muted-foreground text-[18px] leading-relaxed">
            <li><strong className="text-foreground">Accountants and CA firms</strong> receiving monthly supplier invoices as one PDF</li>
            <li><strong className="text-foreground">AP teams</strong> separating supplier invoices before posting</li>
            <li><strong className="text-foreground">Bookkeepers</strong> preparing purchase entries for Tally</li>
            <li><strong className="text-foreground">Founders</strong> organising scanned invoices and documents</li>
          </ul>
        </section>

        {/* Accuracy and limits */}
        <section className="space-y-8">
          <h2 className="text-3xl font-display font-semibold text-foreground">
            Accuracy and limits
          </h2>
          <p className="text-muted-foreground text-[18px] leading-relaxed">
            The AI Invoice Splitter is designed to detect invoice boundaries automatically while keeping you in control of the final split.
          </p>
          <ul className="list-disc list-inside space-y-3 text-muted-foreground text-[18px] leading-relaxed">
            <li><strong className="text-foreground">Scanned bills supported:</strong> Upload scanned invoice PDFs and review the suggested breaks before splitting.</li>
            <li><strong className="text-foreground">Manual corrections:</strong> You can keep, remove or add a break on any page.</li>
            <li><strong className="text-foreground">Variable invoice lengths:</strong> Invoices can be one or multiple pages.</li>
            <li><strong className="text-foreground">Page-by-page review:</strong> Every source page is shown before you download the final files.</li>
            <li><strong className="text-foreground">No invoice data extraction:</strong> The tool only separates the PDF. It does not extract invoice numbers, GSTINs or other invoice fields.</li>
          </ul>
        </section>

        {/* Your files and privacy */}
        <section className="space-y-8">
          <h2 className="text-3xl font-display font-semibold text-foreground">
            Your files and privacy
          </h2>
          <div className="space-y-6 text-muted-foreground text-[18px] leading-relaxed">
            <p>
              Your files are processed to provide the splitting service and are not used to train our AI models. We don't sell your files or share them with third parties for advertising.
            </p>
            <p>
              Your uploaded invoices are not stored after processing.
            </p>
          </div>
        </section>

        {/* FAQs */}
        <section className="space-y-10">
          <h2 className="text-3xl font-display font-semibold text-foreground">
            FAQs
          </h2>

          <div className="space-y-8">
            <div>
              <h3 className="font-semibold text-foreground text-[18px]">How do I split a multi-invoice PDF into separate files?</h3>
              <p className="mt-3 text-muted-foreground text-[16px] leading-relaxed">Upload your combined PDF. AI detects where one invoice ends and the next begins. Review the suggested breaks, make any changes, and download one PDF per invoice in a single ZIP file.</p>
            </div>

            <div>
              <h3 className="font-semibold text-foreground text-[18px]">Can it split invoices with different page counts?</h3>
              <p className="mt-3 text-muted-foreground text-[16px] leading-relaxed">Yes. You can keep, remove or add a break on any page, so invoices can be any length. A one-page invoice followed by a five-page invoice can stay as two separate files.</p>
            </div>

            <div>
              <h3 className="font-semibold text-foreground text-[18px]">How do I split scanned bills?</h3>
              <p className="mt-3 text-muted-foreground text-[16px] leading-relaxed">Scanned bills are supported. Upload your scanned PDF, review the suggested invoice breaks, make any changes, and download the separate PDFs.</p>
            </div>

            <div>
              <h3 className="font-semibold text-foreground text-[18px]">How do I unmerge a combined PDF?</h3>
              <p className="mt-3 text-muted-foreground text-[16px] leading-relaxed">Upload the combined PDF and add a break wherever one document ends. Remove any incorrect suggested breaks, then download the separate PDFs in a ZIP.</p>
            </div>

            <div>
              <h3 className="font-semibold text-foreground text-[18px]">How do I separate the pages of a PDF into individual files?</h3>
              <p className="mt-3 text-muted-foreground text-[16px] leading-relaxed">Add a break after every page. The tool will create a separate PDF for each page and package them into one ZIP file.</p>
            </div>

            <div>
              <h3 className="font-semibold text-foreground text-[18px]">What is the difference between splitting by page and splitting by invoice?</h3>
              <p className="mt-3 text-muted-foreground text-[16px] leading-relaxed">Splitting by page uses specific page numbers or ranges. Splitting by invoice detects where one invoice ends and the next begins, so invoices with different page counts can stay together.</p>
            </div>

            <div>
              <h3 className="font-semibold text-foreground text-[18px]">Is the AI Invoice Splitter free?</h3>
              <p className="mt-3 text-muted-foreground text-[16px] leading-relaxed">Yes, the AI Invoice Splitter is free.</p>
            </div>

            <div>
              <h3 className="font-semibold text-foreground text-[18px]">Does it extract invoice data such as invoice number or GSTIN?</h3>
              <p className="mt-3 text-muted-foreground text-[16px] leading-relaxed">No. The AI Invoice Splitter only separates the PDF. It does not extract invoice fields or create Tally entries. To automate invoice reading and Tally entry, use AI Accountant.</p>
            </div>

            <div>
              <h3 className="font-semibold text-foreground text-[18px]">Can I use the split invoices to make purchase entries in Tally?</h3>
              <p className="mt-3 text-muted-foreground text-[16px] leading-relaxed">Yes. Each invoice becomes a separate PDF that you can match to its purchase entry in Tally. The splitter does not post entries to Tally.</p>
            </div>

            <div>
              <h3 className="font-semibold text-foreground text-[18px]">What do I get after splitting?</h3>
              <p className="mt-3 text-muted-foreground text-[16px] leading-relaxed">You get one PDF per invoice, packed into a single ZIP file.</p>
            </div>

            <div>
              <h3 className="font-semibold text-foreground text-[18px]">Are my invoices stored?</h3>
              <p className="mt-3 text-muted-foreground text-[16px] leading-relaxed">No. Your uploaded invoices are not stored after processing.</p>
            </div>

            <div>
              <h3 className="font-semibold text-foreground text-[18px]">How is this different from iLovePDF or Smallpdf?</h3>
              <p className="mt-3 text-muted-foreground text-[16px] leading-relaxed">Traditional PDF splitters require you to enter the page ranges yourself. This tool detects where one invoice ends and the next begins, and you can review the breaks before splitting.</p>
            </div>
          </div>
        </section>

        {/* Related tools */}
        <section className="space-y-6">
          <h2 className="text-3xl font-display font-semibold text-foreground">
            Related tools
          </h2>
          <ul className="list-disc list-inside space-y-3 text-muted-foreground text-[18px] leading-relaxed">
            <li>PDF to Tally XML Converter</li>
            <li>Bank statement PDF to Tally</li>
            <li>Guide: How to enter purchase invoices in Tally</li>
          </ul>
        </section>

        {/* Split your first PDF */}
        <section className="space-y-8 border border-border bg-muted/50 rounded-2xl p-10 text-center">
          <h2 className="text-4xl font-display font-semibold text-foreground">
            Split your first PDF
          </h2>
          <Button size="lg" className="mt-6 text-[16px] h-12 px-8" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
            Split your first PDF
          </Button>
        </section>

        {/* Have a tool you want us to build? */}
        <section className="space-y-8 border border-border bg-muted/50 rounded-2xl p-10 text-center">
          <h2 className="text-4xl font-display font-semibold text-foreground">
            Have a tool you want us to build?
          </h2>
          <p className="text-muted-foreground text-[18px] leading-relaxed max-w-3xl mx-auto">
            We're building useful tools for accounting and finance teams, from everyday tasks to complex bookkeeping workflows. Have a repetitive accounting task you wish was easier? <strong className="text-foreground">Tell us what you need.</strong> We're bringing useful accounting tools for tasks of all sizes together in one place at AI Accountant.
          </p>
          <Button variant="outline" size="lg" className="mt-6 text-[16px] h-12 px-8">
            Recommend a tool
          </Button>
        </section>

        {/* The latest in accounting, AI and compliance */}
        <section className="space-y-8 border border-border bg-muted/50 rounded-2xl p-10 text-center">
          <h2 className="text-4xl font-display font-semibold text-foreground">
            The latest in accounting, AI and compliance
          </h2>
          <p className="text-muted-foreground text-[18px] leading-relaxed max-w-3xl mx-auto">
            Get useful accounting tools, compliance updates, AI tips and tricks, and practical resources for accountants and finance teams delivered straight to your inbox.
          </p>
          <form className="mt-8 flex max-w-lg mx-auto items-center gap-4">
            <input
              type="email"
              placeholder="Email address"
              className="flex h-12 w-full rounded-md border border-input bg-background px-4 py-2 text-[16px] ring-offset-background file:border-0 file:bg-transparent file:text-[16px] file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            />
            <Button type="submit" size="lg" className="h-12 px-8 text-[16px]">
              Subscribe
            </Button>
          </form>
          <p className="text-[14px] text-muted-foreground mt-6">
            By subscribing, you agree to receive emails from AI Accountant. You can unsubscribe at any time.
          </p>
        </section>

      </div>
    </div>
  )
}

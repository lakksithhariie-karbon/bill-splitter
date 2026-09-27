import { EmptyState } from "@/components/EmptyState"
import { MetricGrid, MetricTile } from "@/components/MetricTile"
import { Toolbar } from "@/components/Toolbar"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

const DOCUMENT_COLUMNS = [
  { key: "document", label: "Document", width: "29%" },
  { key: "uploaded", label: "Uploaded", width: "12%" },
  { key: "pages", label: "Pages", width: "9%" },
  { key: "documents", label: "Documents", width: "11%" },
  { key: "status", label: "Status", width: "15%" },
  { key: "size", label: "Size", width: "10%" },
  { key: "actions", label: "Actions", width: "14%", align: "right" as const },
]

export function DocumentsScreen() {
  return (
    <>
      <MetricGrid>
        <MetricTile label="Total documents" value={0} foot="No uploads yet" />
        <MetricTile label="Needs review" value={0} foot="Nothing in queue" />
        <MetricTile label="Processing" value={0} foot="No active jobs" />
        <MetricTile label="Completed" value={0} foot="No completed splits" />
      </MetricGrid>

      <Card className="gap-0 py-0 [--card-spacing:0px]">
        <Toolbar
          searchPlaceholder="Search file name or document ID…"
          selects={[
            {
              id: "documents-status",
              label: "Status",
              options: [
                "All statuses",
                "Needs review",
                "Processing",
                "Completed",
              ],
            },
            {
              id: "documents-date",
              label: "Date",
              options: ["Last 30 days", "Last 7 days", "Today"],
            },
          ]}
          actionLabel="Export"
        />

        <CardContent className="p-0">
          <div className="w-full overflow-auto">
            <Table className="min-w-[960px] table-fixed">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  {DOCUMENT_COLUMNS.map((column) => (
                    <TableHead
                      key={column.key}
                      style={{ width: column.width }}
                      className={
                        column.align === "right" ? "text-right" : undefined
                      }
                    >
                      {column.label}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow className="hover:bg-transparent">
                  <TableCell colSpan={DOCUMENT_COLUMNS.length} className="p-0">
                    <EmptyState
                      title="No documents yet"
                      body="Upload a PDF to begin."
                      className="m-4 border-solid"
                    />
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>

          <div className="flex min-h-[50px] items-center justify-between gap-2.5 border-t border-border px-2.5 py-2 text-[11px] text-muted-foreground">
            <span>Showing 0 of 0 documents</span>
            <div className="flex gap-1">
              <Button
                type="button"
                variant="outline"
                size="xs"
                className="h-7 text-[10px] font-semibold"
                disabled
              >
                Previous
              </Button>
              <Button
                type="button"
                variant="outline"
                size="xs"
                className="h-7 text-[10px] font-semibold"
                disabled
              >
                Next
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </>
  )
}

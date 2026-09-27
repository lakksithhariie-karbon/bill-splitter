/**
 * Sidebar document list (legacy extraction layout).
 * For the three-column review UI, use {@link GroupPanel} via {@link ReviewScreen}.
 */
export { GroupPanel } from "./GroupPanel"
export type { GroupPanelProps } from "./GroupPanel"

import { Badge } from "@/components/ui/badge"
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import type { DerivedDoc } from "@/lib/split-state"
import type { DuplicateGroupMember, ExtractedDocument, ExtractedDocumentV2 } from "@/lib/types"
import { countUngrounded, countUngroundedV2 } from "@/lib/api"
import { FileText } from "lucide-react"

type Props = {
  documents: DerivedDoc[]
  extraction: ExtractedDocument[]
  extractionV2?: ExtractedDocumentV2[]
  duplicateGroups?: DuplicateGroupMember[][]
  selectedPage: number
  onSelectDoc: (startPage: number) => void
}

type NavEntry = {
  start_page: number
  end_page: number
  doc_type: string
  ungrounded: number
  confidence: number
  needsAttention: boolean
  duplicatePeers: DuplicateGroupMember[]
}

function matchExtraction(
  doc: DerivedDoc,
  extraction: ExtractedDocument[]
): ExtractedDocument | undefined {
  return extraction.find(
    (d) => d.page_start <= doc.end_page && d.page_end >= doc.start_page
  )
}

function peersForDoc(
  start: number,
  end: number,
  groups: DuplicateGroupMember[][]
): DuplicateGroupMember[] {
  for (const group of groups) {
    const mine = group.find((m) => m.page_start === start && m.page_end === end)
    if (!mine) continue
    return group.filter((m) => !(m.page_start === start && m.page_end === end))
  }
  return []
}

function duplicateTooltip(peers: DuplicateGroupMember[]): string {
  const peer = peers[0]
  const label = peer ? `p${peer.page_start}-p${peer.page_end}` : "another doc"
  return `Same bill number as doc ${label}. Possible continuation of one document. Review before saving.`
}

export function DocumentNavigator({
  documents,
  extraction,
  extractionV2,
  duplicateGroups = [],
  selectedPage,
  onSelectDoc,
}: Props) {
  const useV2 = (extractionV2?.length ?? 0) > 0

  const entries: NavEntry[] = useV2
    ? (extractionV2 || []).map((doc) => {
        const ungrounded = countUngroundedV2(doc)
        const confidence = doc.confidence ?? 0
        const duplicatePeers = peersForDoc(
          doc.page_start,
          doc.page_end,
          duplicateGroups
        )
        return {
          start_page: doc.page_start,
          end_page: doc.page_end,
          doc_type: doc.doc_type || "unknown",
          ungrounded,
          confidence,
          needsAttention:
            ungrounded > 0 || confidence < 0.8 || duplicatePeers.length > 0,
          duplicatePeers,
        }
      })
    : documents.map((doc) => {
        const match = matchExtraction(doc, extraction)
        const ungrounded = countUngrounded(match)
        const confidence = match?.confidence ?? 0
        const duplicatePeers = peersForDoc(
          doc.start_page,
          doc.end_page,
          duplicateGroups
        )
        return {
          start_page: doc.start_page,
          end_page: doc.end_page,
          doc_type: doc.doc_type,
          ungrounded,
          confidence,
          needsAttention:
            ungrounded > 0 || confidence < 0.8 || duplicatePeers.length > 0,
          duplicatePeers,
        }
      })

  if (entries.length === 0) return null

  entries.sort((a, b) => {
    if (a.needsAttention !== b.needsAttention) {
      return a.needsAttention ? -1 : 1
    }
    return a.start_page - b.start_page
  })

  return (
    <SidebarGroup>
      <SidebarGroupLabel>Documents</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {entries.map(
            ({
              start_page,
              end_page,
              doc_type,
              ungrounded,
              confidence,
              needsAttention,
              duplicatePeers,
            }) => {
              const active =
                selectedPage >= start_page && selectedPage <= end_page
              return (
                <SidebarMenuItem key={`${start_page}-${end_page}`}>
                  <SidebarMenuButton
                    isActive={active}
                    tooltip={`${doc_type} p${start_page}–${end_page}`}
                    onClick={() => onSelectDoc(start_page)}
                    className={
                      needsAttention
                        ? "border-l-2 border-l-warning text-warning"
                        : undefined
                    }
                  >
                    <FileText aria-hidden="true" />
                    <span className="truncate">
                      {doc_type}
                      <span className="ml-1 text-label num font-normal normal-case tracking-normal text-muted-foreground">
                        p{start_page}–{end_page}
                      </span>
                    </span>
                  </SidebarMenuButton>
                  {duplicatePeers.length > 0 && (
                    <SidebarMenuBadge>
                      <Badge
                        variant="outline"
                        className="h-5 border-warning bg-warning/10 px-1.5 text-sm-ui text-warning"
                        title={duplicateTooltip(duplicatePeers)}
                      >
                        dup
                      </Badge>
                    </SidebarMenuBadge>
                  )}
                  {duplicatePeers.length === 0 && ungrounded > 0 && (
                    <SidebarMenuBadge>
                      <Badge
                        variant="outline"
                        className="h-5 border-warning bg-warning/10 px-1.5 text-sm-ui text-warning"
                      >
                        {ungrounded}
                      </Badge>
                    </SidebarMenuBadge>
                  )}
                  {duplicatePeers.length === 0 &&
                    ungrounded === 0 &&
                    confidence < 0.8 && (
                      <SidebarMenuBadge>
                        <Badge
                          variant="outline"
                          className="h-5 border-border px-1.5 text-sm-ui num text-muted-foreground"
                        >
                          {(confidence * 100).toFixed(0)}%
                        </Badge>
                      </SidebarMenuBadge>
                    )}
                </SidebarMenuItem>
              )
            }
          )}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  )
}

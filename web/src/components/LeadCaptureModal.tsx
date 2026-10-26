import * as React from "react"
import { createPortal } from "react-dom"
import { X } from "lucide-react"

import {
  isValidEmail,
  isValidName,
  isValidPhone,
  markLeadCaptured,
  sanitizePhoneNumber,
  saveLeadContact,
  submitLead,
  type LeadFormData,
} from "@/lib/leadCapture"

export interface LeadCaptureModalProps {
  isOpen: boolean
  onClose: () => void
  onSuccess: () => void
}

export function LeadCaptureModal({
  isOpen,
  onClose,
  onSuccess,
}: LeadCaptureModalProps) {
  const [name, setName] = React.useState("")
  const [phone, setPhone] = React.useState("")
  const [email, setEmail] = React.useState("")
  const [tool, setTool] = React.useState("")

  const [errors, setErrors] = React.useState<{
    name?: string
    phone?: string
    email?: string
    tool?: string
  }>({})

  const nameInputRef = React.useRef<HTMLInputElement>(null)
  const phoneInputRef = React.useRef<HTMLInputElement>(null)
  const emailInputRef = React.useRef<HTMLInputElement>(null)
  const toolSelectRef = React.useRef<HTMLSelectElement>(null)

  // Focus name field on open
  React.useEffect(() => {
    if (isOpen) {
      const t = setTimeout(() => {
        nameInputRef.current?.focus()
      }, 50)
      return () => clearTimeout(t)
    }
  }, [isOpen])

  // Escape key closes modal
  React.useEffect(() => {
    if (!isOpen) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose()
      }
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [isOpen, onClose])

  if (!isOpen) return null
  if (typeof document === "undefined") return null

  const handlePhoneChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const sanitized = sanitizePhoneNumber(e.target.value)
    setPhone(sanitized)
    if (errors.phone) {
      setErrors((prev) => ({ ...prev, phone: undefined }))
    }
  }

  const handleNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setName(e.target.value)
    if (errors.name) {
      setErrors((prev) => ({ ...prev, name: undefined }))
    }
  }

  const handleEmailChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setEmail(e.target.value)
    if (errors.email) {
      setErrors((prev) => ({ ...prev, email: undefined }))
    }
  }

  const handleToolChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setTool(e.target.value)
    if (errors.tool) {
      setErrors((prev) => ({ ...prev, tool: undefined }))
    }
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()

    const newErrors: {
      name?: string
      phone?: string
      email?: string
      tool?: string
    } = {}

    const isNameOk = isValidName(name)
    if (!isNameOk) {
      newErrors.name = "Enter your full name."
    }

    const isPhoneOk = isValidPhone(phone)
    if (!isPhoneOk) {
      newErrors.phone = "Enter a valid 10-digit mobile number."
    }

    const isEmailOk = isValidEmail(email)
    if (!isEmailOk) {
      newErrors.email = "Enter a valid email address."
    }

    const isToolOk = tool.trim().length > 0
    if (!isToolOk) {
      newErrors.tool = "Select the accounting tool you use."
    }

    setErrors(newErrors)

    if (!isNameOk) {
      nameInputRef.current?.focus()
      return
    }
    if (!isPhoneOk) {
      phoneInputRef.current?.focus()
      return
    }
    if (!isEmailOk) {
      emailInputRef.current?.focus()
      return
    }
    if (!isToolOk) {
      toolSelectRef.current?.focus()
      return
    }

    const leadData: LeadFormData = {
      name: name.trim(),
      phone: phone.trim(),
      email: email.trim().toLowerCase(),
      accountingTool: tool,
    }

    // Persist session memory and unlock result immediately
    markLeadCaptured()
    saveLeadContact({
      name: leadData.name,
      email: leadData.email,
      phone: leadData.phone,
    })

    // Fire and forget webhook post
    void submitLead(leadData)

    onSuccess()
  }

  const modalNode = (
    <div
      id="bs-lead-backdrop"
      className="fixed inset-0 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs"
      style={{ zIndex: 2147483000 }}
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          onClose()
        }
      }}
    >
      <div
        id="bs-lead-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="bs-lead-title"
        aria-describedby="bs-lead-subtitle"
        className="relative w-full max-w-[440px] max-h-[calc(100vh-32px)] overflow-y-auto border border-border bg-card p-6 shadow-2xl rounded-md text-foreground"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Close button */}
        <button
          type="button"
          id="bs-lead-close"
          aria-label="Close"
          onClick={onClose}
          className="absolute top-4 right-4 inline-flex size-7 items-center justify-center rounded-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors cursor-pointer"
        >
          <X className="size-4" strokeWidth={2} />
        </button>

        {/* Title & Subtitle */}
        <div className="pr-6">
          <h2
            id="bs-lead-title"
            className="font-display text-lg sm:text-xl font-bold tracking-tight text-foreground leading-snug"
          >
            You{"\u2019"}re one step away from your result
          </h2>
          <p
            id="bs-lead-subtitle"
            className="mt-1.5 text-xs text-muted-foreground leading-relaxed"
          >
            Your split invoices are ready. Share a few details to see the full
            breakdown, review suggested breaks, and download your split PDFs.
          </p>
        </div>

        {/* Lead form */}
        <form onSubmit={handleSubmit} className="mt-4 flex flex-col gap-3">
          {/* Full Name */}
          <div>
            <input
              ref={nameInputRef}
              id="bs-lead-name"
              type="text"
              autoComplete="name"
              placeholder="Full name"
              aria-label="Full name"
              value={name}
              onChange={handleNameChange}
              className={`w-full h-10 px-3 text-xs bg-background border ${
                errors.name
                  ? "border-destructive focus:border-destructive"
                  : "border-border focus:border-foreground"
              } rounded-md outline-none transition-colors placeholder:text-muted-foreground`}
            />
            {errors.name ? (
              <p
                id="bs-lead-name-error"
                role="alert"
                className="mt-1 text-[11px] font-medium text-destructive leading-tight"
              >
                {errors.name}
              </p>
            ) : null}
          </div>

          {/* Mobile Number */}
          <div>
            <input
              ref={phoneInputRef}
              id="bs-lead-phone"
              type="tel"
              inputMode="numeric"
              maxLength={10}
              autoComplete="tel-national"
              placeholder="Mobile number (10 digits)"
              aria-label="Mobile number (10 digits)"
              value={phone}
              onChange={handlePhoneChange}
              className={`w-full h-10 px-3 text-xs bg-background border ${
                errors.phone
                  ? "border-destructive focus:border-destructive"
                  : "border-border focus:border-foreground"
              } rounded-md outline-none transition-colors placeholder:text-muted-foreground`}
            />
            {errors.phone ? (
              <p
                id="bs-lead-phone-error"
                role="alert"
                className="mt-1 text-[11px] font-medium text-destructive leading-tight"
              >
                {errors.phone}
              </p>
            ) : null}
          </div>

          {/* Work Email */}
          <div>
            <input
              ref={emailInputRef}
              id="bs-lead-email"
              type="email"
              autoComplete="email"
              placeholder="Work email"
              aria-label="Work email"
              value={email}
              onChange={handleEmailChange}
              className={`w-full h-10 px-3 text-xs bg-background border ${
                errors.email
                  ? "border-destructive focus:border-destructive"
                  : "border-border focus:border-foreground"
              } rounded-md outline-none transition-colors placeholder:text-muted-foreground`}
            />
            {errors.email ? (
              <p
                id="bs-lead-email-error"
                role="alert"
                className="mt-1 text-[11px] font-medium text-destructive leading-tight"
              >
                {errors.email}
              </p>
            ) : null}
          </div>

          {/* Accounting Tool Select */}
          <div>
            <select
              ref={toolSelectRef}
              id="bs-lead-tool"
              required
              aria-label="Which accounting tool do you use?"
              value={tool}
              onChange={handleToolChange}
              className={`w-full h-10 px-3 text-xs bg-background border ${
                errors.tool
                  ? "border-destructive focus:border-destructive"
                  : "border-border focus:border-foreground"
              } rounded-md outline-none transition-colors ${
                tool ? "text-foreground" : "text-muted-foreground"
              }`}
            >
              <option value="" disabled>
                Which accounting tool do you use?
              </option>
              <option value="Tally" className="text-foreground">
                Tally
              </option>
              <option value="Both" className="text-foreground">
                Both
              </option>
              <option value="Zoho Books" className="text-foreground">
                Zoho Books
              </option>
              <option value="Others" className="text-foreground">
                Others
              </option>
            </select>
            {errors.tool ? (
              <p
                id="bs-lead-tool-error"
                role="alert"
                className="mt-1 text-[11px] font-medium text-destructive leading-tight"
              >
                {errors.tool}
              </p>
            ) : null}
          </div>

          {/* Submit Button */}
          <button
            type="submit"
            id="bs-lead-submit"
            className="mt-1 h-10 w-full bg-primary text-primary-foreground text-xs font-semibold rounded-md hover:opacity-90 active:scale-[0.99] transition-all cursor-pointer flex items-center justify-center"
          >
            Show my result
          </button>

          {/* Consent line */}
          <p
            id="bs-lead-consent"
            className="text-[11px] text-center text-muted-foreground leading-normal"
          >
            By submitting, you agree to be contacted by AI Accountant about our
            product.
          </p>
        </form>
      </div>
    </div>
  )

  return createPortal(modalNode, document.body)
}

export default function Brand() {
  return (
    <a className="brand" href="/" aria-label="TenderWise dashboard">
      <span className="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 40 40" role="img" focusable="false">
          <path d="M11 7.5h12.5L30 14v18.5H11z" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinejoin="round" />
          <path d="M23 7.5V14h7" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinejoin="round" />
          <path d="m15.5 23 3.2 3.2 6.8-7" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M15 17.5h4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      </span>
      <span className="brand-wordmark"><strong>TENDER</strong><span>WISE</span></span>
    </a>
  )
}

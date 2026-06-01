export default function ToggleSwitch({ checked, onChange, label, title, disabled = false }) {
  return (
    <button
      type="button"
      onClick={onChange}
      className={`toggle-switch ${checked ? 'is-on' : ''}`}
      aria-pressed={checked}
      aria-label={label}
      title={title || label}
      disabled={disabled}
    >
      <span />
    </button>
  )
}

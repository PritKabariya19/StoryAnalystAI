import { Chrome } from "lucide-react";

export default function GoogleButton({ onClick, disabled }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="w-full flex items-center justify-center gap-3 py-3 rounded-xl border border-surface-border text-sm font-medium text-white/70 hover:text-white hover:bg-white/10 transition-all mb-6"
    >
      <Chrome size={18} />
      Continue with Google
    </button>
  );
}

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Mail, Lock, User, Eye, EyeOff } from "lucide-react";
import Button from "../Button";

export default function AuthForm({ tab, form, onChange, onSubmit, onForgotPassword, submitting }) {
  const [showPw, setShowPw] = useState(false);

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <AnimatePresence>
        {tab === "signup" && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
          >
            <label className="block text-xs font-medium text-white/50 mb-1.5" htmlFor="name">
              Full Name
            </label>
            <div className="relative">
              <User size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/30" />
              <input
                id="name"
                name="name"
                type="text"
                value={form.name}
                onChange={onChange}
                placeholder="John Doe"
                required={tab === "signup"}
                className="input-field pl-10"
                autoComplete="name"
              />
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <div>
        <label className="block text-xs font-medium text-white/50 mb-1.5" htmlFor="email">
          Email Address
        </label>
        <div className="relative">
          <Mail size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/30" />
          <input
            id="email"
            name="email"
            type="email"
            value={form.email}
            onChange={onChange}
            placeholder="you@example.com"
            required
            className="input-field pl-10"
            autoComplete="email"
          />
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="block text-xs font-medium text-white/50" htmlFor="password">
            Password
          </label>
          {tab === "login" && (
            <button
              type="button"
              onClick={onForgotPassword}
              className="text-xs text-brand-blue hover:underline font-medium"
            >
              Forgot password?
            </button>
          )}
        </div>
        <div className="relative">
          <Lock size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/30" />
          <input
            id="password"
            name="password"
            type={showPw ? "text" : "password"}
            value={form.password}
            onChange={onChange}
            placeholder={tab === "signup" ? "Min 6 characters" : "Your password"}
            required
            minLength={tab === "signup" ? 6 : undefined}
            className="input-field pl-10 pr-10"
            autoComplete={tab === "signup" ? "new-password" : "current-password"}
          />
          <button
            type="button"
            onClick={() => setShowPw((v) => !v)}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-white/30 hover:text-white/60 transition-colors"
            aria-label={showPw ? "Hide password" : "Show password"}
          >
            {showPw ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        </div>
      </div>

      <Button type="submit" variant="primary" loading={submitting} className="w-full mt-2">
        {tab === "login" ? "Sign In" : "Create Account"}
      </Button>
    </form>
  );
}

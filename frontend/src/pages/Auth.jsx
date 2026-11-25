import { useState, useEffect } from "react";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import { Brain, AlertCircle } from "lucide-react";
import useStore from "../state/store";
import AuthForm from "../components/auth/AuthForm";
import GoogleButton from "../components/auth/GoogleButton";

export default function Auth() {
  const [params] = useSearchParams();
  const [tab, setTab] = useState(params.get("tab") === "signup" ? "signup" : "login");
  const [form, setForm] = useState({ name: "", email: "", password: "" });

  const {
    login, signup, loginWithGoogle, resetPassword, clearAuthError,
    authError, authSubmitting, user, addToast,
  } = useStore();
  const navigate = useNavigate();

  useEffect(() => { if (user) navigate("/dashboard", { replace: true }); }, [user]);

  useEffect(() => { clearAuthError(); }, [tab]);

  async function handleForgotPassword() {
    if (!form.email.trim()) { addToast("Enter your email address first.", "error"); return; }
    try {
      await resetPassword(form.email.trim());
      addToast("If an account exists for that email, a reset link has been sent.", "success");
    } catch {
      // Error in store
    }
  }

  function handleChange(e) {
    setForm((f) => ({ ...f, [e.target.name]: e.target.value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    try {
      if (tab === "login") {
        await login(form.email, form.password);
      } else {
        if (!form.name.trim()) { addToast("Name is required.", "error"); return; }
        await signup(form.email, form.password, form.name);
      }
      addToast(`Welcome${tab === "signup" ? " to StoryAnalyst AI" : " back"}!`, "success");
      navigate("/dashboard");
    } catch {
      // Error already in store as authError
    }
  }

  async function handleGoogle() {
    try {
      await loginWithGoogle();
      addToast("Signed in with Google!", "success");
      navigate("/dashboard");
    } catch {
      // Error in store
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4 pt-16">
      {/* Background */}
      <div className="absolute top-1/3 left-1/2 -translate-x-1/2 w-[600px] h-[400px] bg-brand-gradient opacity-10 blur-3xl rounded-full pointer-events-none" />

      <motion.div
        initial={{ opacity: 0, y: 30 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="glass-strong rounded-3xl p-8 w-full max-w-md relative z-10"
      >
        {/* Logo */}
        <div className="text-center mb-8">
          <Link to="/" className="inline-flex items-center gap-2.5 mb-4">
            <div className="w-10 h-10 rounded-xl bg-brand-gradient flex items-center justify-center shadow-glow">
              <Brain size={20} className="text-white" />
            </div>
            <span className="font-display font-bold text-xl gradient-text">StoryAnalyst AI</span>
          </Link>
          <h1 className="text-2xl font-bold text-white">
            {tab === "login" ? "Welcome back" : "Create your account"}
          </h1>
          <p className="text-white/40 text-sm mt-1">
            {tab === "login" ? "Sign in to your workspace" : "Start your free QA journey"}
          </p>
        </div>

        {/* Tab Toggle */}
        <div className="flex rounded-xl p-1 mb-6" style={{ background: "rgba(255,255,255,0.05)" }}>
          {["login", "signup"].map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`flex-1 py-2 rounded-lg text-sm font-semibold transition-all duration-200 ${
                tab === t ? "bg-brand-gradient text-white shadow-glow" : "text-white/40 hover:text-white/70"
              }`}
            >
              {t === "login" ? "Sign In" : "Sign Up"}
            </button>
          ))}
        </div>

        <GoogleButton onClick={handleGoogle} disabled={authSubmitting} />

        <div className="flex items-center gap-3 mb-6">
          <div className="flex-1 h-px bg-surface-border" />
          <span className="text-white/30 text-xs">or continue with email</span>
          <div className="flex-1 h-px bg-surface-border" />
        </div>

        {/* Error */}
        <AnimatePresence>
          {authError && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-xl px-3 py-2.5 text-sm text-red-400 mb-4"
            >
              <AlertCircle size={15} className="flex-shrink-0" />
              {authError}
            </motion.div>
          )}
        </AnimatePresence>

        <AuthForm
          tab={tab}
          form={form}
          onChange={handleChange}
          onSubmit={handleSubmit}
          onForgotPassword={handleForgotPassword}
          submitting={authSubmitting}
        />

        <p className="text-center text-xs text-white/30 mt-6">
          {tab === "login" ? "Don't have an account? " : "Already have an account? "}
          <button
            className="text-brand-blue hover:underline font-medium"
            onClick={() => setTab(tab === "login" ? "signup" : "login")}
          >
            {tab === "login" ? "Sign Up Free" : "Sign In"}
          </button>
        </p>
      </motion.div>
    </div>
  );
}

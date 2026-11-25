// Firebase Authentication calls. Local (Django) auth lives in api.js.
import {
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  sendPasswordResetEmail,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut,
  updateProfile,
} from "firebase/auth";
import { auth, googleProvider } from "./firebase";

const ERROR_MESSAGES = {
  "auth/email-already-in-use": "Email is already in use.",
  "auth/invalid-email": "Enter a valid email address.",
  "auth/weak-password": "Password must be at least 6 characters.",
  "auth/invalid-credential": "Invalid email or password.",
  "auth/user-disabled": "This account has been disabled.",
  "auth/too-many-requests": "Too many attempts. Please try again later.",
  "auth/network-request-failed": "Network error. Please check your connection.",
  "auth/popup-blocked": "Your browser blocked the sign-in popup. Allow popups and try again.",
  "auth/unauthorized-domain": "This domain is not authorized for sign-in in Firebase.",
  "auth/operation-not-allowed": "This sign-in method is not enabled in Firebase.",
  "auth/account-exists-with-different-credential": "This email already uses a different sign-in method.",
};

export const authErrorMessage = (err) =>
  ERROR_MESSAGES[err.code] || (err.code ? "Authentication failed. Please try again." : err.message);

export const isBadCredential = (err) =>
  ["auth/invalid-credential", "auth/user-not-found", "auth/wrong-password"].includes(err.code);

export const isPopupDismissed = (err) =>
  ["auth/popup-closed-by-user", "auth/cancelled-popup-request"].includes(err.code);

export const toUser = (firebaseUser) => ({
  uid: firebaseUser.uid,
  email: firebaseUser.email,
  displayName: firebaseUser.displayName,
  photoURL: firebaseUser.photoURL,
});

export async function signUpWithEmail(email, password, name) {
  const { user } = await createUserWithEmailAndPassword(auth, email, password);
  await updateProfile(user, { displayName: name });
  await user.getIdToken(true); // reissue the token so it carries the name
  return toUser(user);
}

export const signInWithEmail = (email, password) => signInWithEmailAndPassword(auth, email, password);

export const signInWithGoogle = () => signInWithPopup(auth, googleProvider);

export const resetPassword = (email) => sendPasswordResetEmail(auth, email);

export const signOutFirebase = () => signOut(auth);

export const watchAuthState = (callback) => onAuthStateChanged(auth, callback);

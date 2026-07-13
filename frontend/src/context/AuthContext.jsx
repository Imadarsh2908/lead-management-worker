import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { api, logout } from '../services/api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const currentUser = api.getCurrentUser();
    if (currentUser) {
      setUser(currentUser);
    }
    setLoading(false);

    const handleLogout = () => setUser(null);
    window.addEventListener('auth-logout', handleLogout);
    return () => window.removeEventListener('auth-logout', handleLogout);
  }, []);

  const login = useCallback(async (username, password) => {
    const decoded = await api.login(username, password);
    setUser({ username: decoded.sub, role: decoded.role });
    return { username: decoded.sub, role: decoded.role };
  }, []);

  const signOut = useCallback(() => {
    logout();
    setUser(null);
  }, []);

  const canViewLeads = user?.role === 'Admin' || user?.role === 'Sales';
  const canDeleteLeads = user?.role === 'Admin';

  return (
    <AuthContext.Provider value={{ user, loading, login, signOut, canViewLeads, canDeleteLeads }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}

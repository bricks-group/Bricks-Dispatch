// ThemeContext — Light/Dark mode for Bricks GO app
// Dispatch defaults to dark theme (per BRICKS.md section 14)
import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { useColorScheme } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

export interface DispatchTheme {
  // Surfaces
  bg: string;
  surface: string;
  surfaceDeep: string;
  border: string;
  borderStrong: string;
  // Text
  text: string;
  textSecondary: string;
  textMuted: string;
  textFaint: string;
  // Accent
  brick: string;
  brickDeep: string;
  brickSoft: string;
  brickTint: string;
  // Status
  ok: string;
  okTint: string;
  warn: string;
  warnTint: string;
  bad: string;
  badTint: string;
  // Meta
  isDark: boolean;
}

const DARK_THEME: DispatchTheme = {
  bg: '#141210',
  surface: '#1C1A17',
  surfaceDeep: '#23201C',
  border: '#F4F1EB14',
  borderStrong: '#F4F1EB26',
  text: '#F4F1EB',
  textSecondary: '#D6CFC2',
  textMuted: '#8F8678',
  textFaint: '#5E574C',
  brick: '#D2542B',
  brickDeep: '#A8401F',
  brickSoft: '#3A1A10',
  brickTint: '#2A1510',
  ok: '#4A6B3C',
  okTint: '#1A2E15',
  warn: '#B8741F',
  warnTint: '#2E2515',
  bad: '#8C2F24',
  badTint: '#2A1510',
  isDark: true,
};

const LIGHT_THEME: DispatchTheme = {
  bg: '#F4F1EB',
  surface: '#EDE8DF',
  surfaceDeep: '#E4DED2',
  border: '#14111020',
  borderStrong: '#14111038',
  text: '#14110E',
  textSecondary: '#6B6054',
  textMuted: '#A39684',
  textFaint: '#C8BDAE',
  brick: '#D2542B',
  brickDeep: '#A8401F',
  brickSoft: '#F4D9CC',
  brickTint: '#FBEFE8',
  ok: '#4A6B3C',
  okTint: '#E6ECDD',
  warn: '#B8741F',
  warnTint: '#F6E8D1',
  bad: '#8C2F24',
  badTint: '#F1DAD4',
  isDark: false,
};

interface ThemeContextType {
  theme: DispatchTheme;
  isDark: boolean;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextType>({
  theme: DARK_THEME,
  isDark: true,
  toggleTheme: () => {},
});

const THEME_KEY = '@bricks_dispatch_theme';

export function ThemeProvider({ children }: { children: ReactNode }) {
  const systemScheme = useColorScheme();
  // Dispatch defaults to dark, unless user has saved preference
  const [isDark, setIsDark] = useState(true);

  useEffect(() => {
    AsyncStorage.getItem(THEME_KEY).then(saved => {
      if (saved === 'light') setIsDark(false);
      else if (saved === 'dark') setIsDark(true);
      else setIsDark(systemScheme !== 'light'); // default to dark unless system is explicitly light
    });
  }, [systemScheme]);

  const toggleTheme = () => {
    const next = !isDark;
    setIsDark(next);
    AsyncStorage.setItem(THEME_KEY, next ? 'dark' : 'light');
  };

  const theme = isDark ? DARK_THEME : LIGHT_THEME;

  return (
    <ThemeContext.Provider value={{ theme, isDark, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}

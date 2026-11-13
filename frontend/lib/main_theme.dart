import 'package:flutter/material.dart';

ThemeData get mainTheme => ThemeData(
  useMaterial3: true,
  brightness: Brightness.dark,
  colorScheme:
      ColorScheme.fromSeed(
        seedColor: const Color(0xff64b5f6),
        brightness: Brightness.dark,
      ).copyWith(
        primary: const Color(0xff64b5f6),
        onPrimary: const Color(0xff0e1117),
        primaryContainer: const Color(0xff1e3a56),
        onPrimaryContainer: const Color(0xfffafafa),
        secondary: const Color(0xff90caf9),
        surface: const Color(0xff181c22),
        surfaceContainerLowest: const Color(0xff0e1117),
        surfaceContainerLow: const Color(0xff181c22),
        surfaceContainer: const Color(0xff20242b),
        surfaceContainerHigh: const Color(0xff262730),
        surfaceContainerHighest: const Color(0xff30333d),
        onSurface: const Color(0xfffafafa),
        onSurfaceVariant: const Color(0xffb5b8c2),
        outline: const Color(0xff555963),
        outlineVariant: const Color(0xff353941),
        error: const Color(0xffff6b6b),
      ),
  scaffoldBackgroundColor: const Color(0xff0e1117),
  appBarTheme: const AppBarTheme(
    backgroundColor: Color(0xff0e1117),
    foregroundColor: Color(0xfffafafa),
    surfaceTintColor: Colors.transparent,
  ),
  dialogTheme: const DialogThemeData(
    backgroundColor: Color(0xff181c22),
    surfaceTintColor: Colors.transparent,
  ),
  inputDecorationTheme: const InputDecorationTheme(
    border: OutlineInputBorder(),
    isDense: true,
    filled: true,
    fillColor: Color(0xff262730),
  ),
  cardTheme: const CardThemeData(
    color: Color(0xff181c22),
    surfaceTintColor: Colors.transparent,
    margin: EdgeInsets.symmetric(vertical: 4),
    elevation: 0,
  ),
  dividerColor: const Color(0xff353941),
);

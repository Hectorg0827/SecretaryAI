import React, { useState, useRef } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  KeyboardAvoidingView,
  Platform,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useAuthStore } from '../stores/authStore';
import { COLORS, RADIUS, SHADOW } from '../theme';

export default function LoginScreen() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [totpCode, setTotpCode] = useState('');
  const totpRef = useRef<TextInput>(null);

  const { login, submitTotp, cancel2fa, needs2fa, isLoading, error } = useAuthStore();

  const handleLogin = async () => {
    if (!email.trim() || !password.trim()) {
      Alert.alert('Missing fields', 'Please enter your email and password.');
      return;
    }
    try {
      await login(email.trim(), password);
    } catch {
      // error shown via store.error
    }
  };

  const handleTotp = async () => {
    if (totpCode.trim().length !== 6) {
      Alert.alert('Invalid code', 'Enter the 6-digit code from your authenticator app.');
      return;
    }
    try {
      await submitTotp(totpCode.trim());
    } catch {
      setTotpCode('');
      totpRef.current?.focus();
    }
  };

  const brandBlock = (
    <View style={styles.brandRow}>
      <View style={styles.logoBox}>
        <Text style={styles.logoLetter}>S</Text>
      </View>
      <Text style={styles.brandName}>Secretary AI</Text>
    </View>
  );

  if (needs2fa) {
    return (
      <SafeAreaView style={styles.container}>
        <KeyboardAvoidingView
          style={styles.inner}
          behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        >
          {brandBlock}
          <Text style={styles.subtitle}>Two-factor authentication</Text>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>Enter your 2FA code</Text>
            <Text style={styles.hint}>
              Open your authenticator app and enter the 6-digit code.
            </Text>

            {error ? (
              <View style={styles.errorBox}>
                <Text style={styles.errorText}>{error}</Text>
              </View>
            ) : null}

            <View style={styles.field}>
              <Text style={styles.label}>Verification code</Text>
              <TextInput
                ref={totpRef}
                style={[styles.input, styles.totpInput]}
                value={totpCode}
                onChangeText={(t) => setTotpCode(t.replace(/\D/g, '').slice(0, 6))}
                placeholder="000000"
                placeholderTextColor={COLORS.textMuted}
                keyboardType="number-pad"
                returnKeyType="done"
                onSubmitEditing={handleTotp}
                editable={!isLoading}
                autoFocus
              />
            </View>

            <TouchableOpacity
              style={[styles.button, isLoading && styles.buttonDisabled]}
              onPress={handleTotp}
              disabled={isLoading}
              activeOpacity={0.85}
            >
              {isLoading ? (
                <ActivityIndicator color="#fff" size="small" />
              ) : (
                <Text style={styles.buttonText}>Verify</Text>
              )}
            </TouchableOpacity>

            <TouchableOpacity style={styles.cancelBtn} onPress={cancel2fa}>
              <Text style={styles.cancelText}>← Back to sign in</Text>
            </TouchableOpacity>
          </View>
        </KeyboardAvoidingView>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <KeyboardAvoidingView
        style={styles.inner}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      >
        {brandBlock}
        <Text style={styles.subtitle}>Your AI-powered business assistant</Text>

        <View style={styles.card}>
          <Text style={styles.cardTitle}>Sign in</Text>

          {error ? (
            <View style={styles.errorBox}>
              <Text style={styles.errorText}>{error}</Text>
            </View>
          ) : null}

          <View style={styles.field}>
            <Text style={styles.label}>Email</Text>
            <TextInput
              style={styles.input}
              value={email}
              onChangeText={setEmail}
              placeholder="you@company.com"
              placeholderTextColor={COLORS.textMuted}
              keyboardType="email-address"
              autoCapitalize="none"
              autoCorrect={false}
              returnKeyType="next"
              editable={!isLoading}
            />
          </View>

          <View style={styles.field}>
            <Text style={styles.label}>Password</Text>
            <TextInput
              style={styles.input}
              value={password}
              onChangeText={setPassword}
              placeholder="••••••••"
              placeholderTextColor={COLORS.textMuted}
              secureTextEntry
              returnKeyType="done"
              onSubmitEditing={handleLogin}
              editable={!isLoading}
            />
          </View>

          <TouchableOpacity
            style={[styles.button, isLoading && styles.buttonDisabled]}
            onPress={handleLogin}
            disabled={isLoading}
            activeOpacity={0.85}
          >
            {isLoading ? (
              <ActivityIndicator color="#fff" size="small" />
            ) : (
              <Text style={styles.buttonText}>Sign in</Text>
            )}
          </TouchableOpacity>
        </View>

        <Text style={styles.footer}>
          Contact your administrator to set up your account.
        </Text>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
  },
  inner: {
    flex: 1,
    justifyContent: 'center',
    paddingHorizontal: 24,
  },
  brandRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 8,
  },
  logoBox: {
    width: 44,
    height: 44,
    borderRadius: RADIUS.md,
    backgroundColor: COLORS.primary,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 10,
  },
  logoLetter: {
    color: '#fff',
    fontSize: 22,
    fontWeight: '700',
  },
  brandName: {
    fontSize: 26,
    fontWeight: '700',
    color: COLORS.text,
  },
  subtitle: {
    textAlign: 'center',
    color: COLORS.textSecondary,
    fontSize: 14,
    marginBottom: 32,
  },
  card: {
    backgroundColor: COLORS.surface,
    borderRadius: RADIUS.lg,
    padding: 24,
    ...SHADOW.md,
  },
  cardTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: COLORS.text,
    marginBottom: 16,
  },
  hint: {
    fontSize: 13,
    color: COLORS.textSecondary,
    marginBottom: 16,
  },
  errorBox: {
    backgroundColor: COLORS.dangerLight,
    borderRadius: RADIUS.sm,
    padding: 10,
    marginBottom: 12,
  },
  errorText: {
    color: COLORS.danger,
    fontSize: 13,
  },
  field: {
    marginBottom: 14,
  },
  label: {
    fontSize: 13,
    fontWeight: '600',
    color: COLORS.text,
    marginBottom: 6,
  },
  input: {
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: RADIUS.md,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
    color: COLORS.text,
    backgroundColor: COLORS.background,
  },
  totpInput: {
    fontSize: 24,
    letterSpacing: 8,
    textAlign: 'center',
  },
  button: {
    backgroundColor: COLORS.primary,
    borderRadius: RADIUS.md,
    paddingVertical: 13,
    alignItems: 'center',
    marginTop: 6,
  },
  buttonDisabled: {
    opacity: 0.7,
  },
  buttonText: {
    color: '#fff',
    fontWeight: '700',
    fontSize: 15,
  },
  cancelBtn: {
    marginTop: 16,
    alignItems: 'center',
  },
  cancelText: {
    color: COLORS.primary,
    fontSize: 14,
    fontWeight: '600',
  },
  footer: {
    marginTop: 24,
    textAlign: 'center',
    color: COLORS.textMuted,
    fontSize: 12,
  },
});

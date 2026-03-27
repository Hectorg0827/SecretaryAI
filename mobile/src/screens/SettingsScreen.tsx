import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  TextInput,
  Alert,
  ScrollView,
  Switch,
  ActivityIndicator,
  Linking,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useAuthStore } from '../stores/authStore';
import { setApiBase, API_BASE } from '../api/client';
import { setup2fa, verify2fa, disable2fa } from '../api/auth';
import { COLORS, RADIUS, SHADOW } from '../theme';

const ROLE_LABELS: Record<string, string> = {
  owner: 'Owner',
  manager: 'Manager',
  sales_rep: 'Sales Rep',
  back_office: 'Back Office',
  viewer: 'Viewer',
};

function SettingRow({
  icon,
  label,
  value,
  onPress,
  danger,
  right,
}: {
  icon: string;
  label: string;
  value?: string;
  onPress?: () => void;
  danger?: boolean;
  right?: React.ReactNode;
}) {
  return (
    <TouchableOpacity
      style={styles.row}
      onPress={onPress}
      disabled={!onPress && !right}
      activeOpacity={onPress ? 0.7 : 1}
    >
      <View style={[styles.rowIcon, { backgroundColor: danger ? COLORS.dangerLight : COLORS.primaryLight }]}>
        <Ionicons name={icon as any} size={18} color={danger ? COLORS.danger : COLORS.primary} />
      </View>
      <View style={styles.rowContent}>
        <Text style={[styles.rowLabel, danger && { color: COLORS.danger }]}>{label}</Text>
        {value ? <Text style={styles.rowValue}>{value}</Text> : null}
      </View>
      {right ?? (onPress ? <Ionicons name="chevron-forward" size={16} color={COLORS.textMuted} /> : null)}
    </TouchableOpacity>
  );
}

type TwoFAStep = 'idle' | 'setup' | 'verify' | 'disabling';

function TwoFASection({ totpEnabled }: { totpEnabled: boolean }) {
  const [step, setStep] = useState<TwoFAStep>('idle');
  const [secret, setSecret] = useState('');
  const [otpauthUrl, setOtpauthUrl] = useState('');
  const [code, setCode] = useState('');
  const [loading, setLoading] = useState(false);
  const [enabled, setEnabled] = useState(totpEnabled);

  const handleSetup = async () => {
    setLoading(true);
    try {
      const res = await setup2fa();
      setSecret(res.secret);
      setOtpauthUrl(res.otpauth_url);
      setStep('setup');
    } catch (err) {
      Alert.alert('Error', err instanceof Error ? err.message : 'Failed to start 2FA setup');
    } finally {
      setLoading(false);
    }
  };

  const handleOpenAuthApp = () => {
    Linking.openURL(otpauthUrl).catch(() => {
      Alert.alert('Cannot open URL', 'Copy the secret and enter it manually in your authenticator app.');
    });
  };

  const handleVerify = async () => {
    if (code.trim().length !== 6) {
      Alert.alert('Invalid code', 'Enter the 6-digit code from your authenticator app.');
      return;
    }
    setLoading(true);
    try {
      await verify2fa(code.trim());
      setEnabled(true);
      setStep('idle');
      setCode('');
      Alert.alert('2FA enabled', 'Two-factor authentication is now active on your account.');
    } catch (err) {
      Alert.alert('Invalid code', err instanceof Error ? err.message : 'Please try again.');
      setCode('');
    } finally {
      setLoading(false);
    }
  };

  const handleDisable = async () => {
    if (code.trim().length !== 6) {
      Alert.alert('Invalid code', 'Enter the 6-digit code from your authenticator app.');
      return;
    }
    setLoading(true);
    try {
      await disable2fa(code.trim());
      setEnabled(false);
      setStep('idle');
      setCode('');
      Alert.alert('2FA disabled', 'Two-factor authentication has been removed from your account.');
    } catch (err) {
      Alert.alert('Invalid code', err instanceof Error ? err.message : 'Please try again.');
      setCode('');
    } finally {
      setLoading(false);
    }
  };

  if (step === 'setup') {
    return (
      <View style={twoFAStyles.container}>
        <Text style={twoFAStyles.title}>Set up 2FA</Text>
        <Text style={twoFAStyles.body}>
          Tap the button below to open your authenticator app, or copy the secret and enter it
          manually. Then enter the 6-digit code to verify.
        </Text>
        <TouchableOpacity style={twoFAStyles.qrBtn} onPress={handleOpenAuthApp}>
          <Ionicons name="qr-code" size={18} color={COLORS.primary} />
          <Text style={twoFAStyles.qrBtnText}>Open in authenticator app</Text>
        </TouchableOpacity>
        <Text style={twoFAStyles.secretLabel}>Manual secret</Text>
        <Text style={twoFAStyles.secret} selectable>{secret}</Text>
        <Text style={twoFAStyles.label}>Verification code</Text>
        <TextInput
          style={[twoFAStyles.input, twoFAStyles.codeInput]}
          value={code}
          onChangeText={(t) => setCode(t.replace(/\D/g, '').slice(0, 6))}
          placeholder="000000"
          placeholderTextColor={COLORS.textMuted}
          keyboardType="number-pad"
          editable={!loading}
        />
        <View style={twoFAStyles.row}>
          <TouchableOpacity
            style={[twoFAStyles.btn, twoFAStyles.cancelBtn]}
            onPress={() => { setStep('idle'); setCode(''); }}
          >
            <Text style={twoFAStyles.cancelText}>Cancel</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[twoFAStyles.btn, twoFAStyles.primaryBtn, loading && twoFAStyles.disabledBtn]}
            onPress={handleVerify}
            disabled={loading}
          >
            {loading ? (
              <ActivityIndicator color="#fff" size="small" />
            ) : (
              <Text style={twoFAStyles.primaryText}>Verify & Enable</Text>
            )}
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  if (step === 'disabling') {
    return (
      <View style={twoFAStyles.container}>
        <Text style={twoFAStyles.title}>Disable 2FA</Text>
        <Text style={twoFAStyles.body}>
          Enter the 6-digit code from your authenticator app to confirm you want to disable 2FA.
        </Text>
        <Text style={twoFAStyles.label}>Verification code</Text>
        <TextInput
          style={[twoFAStyles.input, twoFAStyles.codeInput]}
          value={code}
          onChangeText={(t) => setCode(t.replace(/\D/g, '').slice(0, 6))}
          placeholder="000000"
          placeholderTextColor={COLORS.textMuted}
          keyboardType="number-pad"
          editable={!loading}
          autoFocus
        />
        <View style={twoFAStyles.row}>
          <TouchableOpacity
            style={[twoFAStyles.btn, twoFAStyles.cancelBtn]}
            onPress={() => { setStep('idle'); setCode(''); }}
          >
            <Text style={twoFAStyles.cancelText}>Cancel</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[twoFAStyles.btn, twoFAStyles.dangerBtn, loading && twoFAStyles.disabledBtn]}
            onPress={handleDisable}
            disabled={loading}
          >
            {loading ? (
              <ActivityIndicator color="#fff" size="small" />
            ) : (
              <Text style={twoFAStyles.primaryText}>Disable 2FA</Text>
            )}
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  // idle — show enable/disable button
  return (
    <View style={twoFAStyles.container}>
      <View style={twoFAStyles.statusRow}>
        <Ionicons
          name={enabled ? 'shield-checkmark' : 'shield-outline'}
          size={20}
          color={enabled ? COLORS.success : COLORS.textMuted}
        />
        <Text style={[twoFAStyles.statusText, { color: enabled ? COLORS.success : COLORS.textMuted }]}>
          {enabled ? '2FA is enabled' : '2FA is not enabled'}
        </Text>
      </View>
      {enabled ? (
        <TouchableOpacity
          style={[twoFAStyles.btn, twoFAStyles.dangerOutlineBtn]}
          onPress={() => setStep('disabling')}
        >
          <Text style={twoFAStyles.dangerOutlineText}>Disable two-factor authentication</Text>
        </TouchableOpacity>
      ) : (
        <TouchableOpacity
          style={[twoFAStyles.btn, twoFAStyles.primaryBtn, loading && twoFAStyles.disabledBtn]}
          onPress={handleSetup}
          disabled={loading}
        >
          {loading ? (
            <ActivityIndicator color="#fff" size="small" />
          ) : (
            <Text style={twoFAStyles.primaryText}>Enable two-factor authentication</Text>
          )}
        </TouchableOpacity>
      )}
    </View>
  );
}

export default function SettingsScreen() {
  const { user, logout } = useAuthStore();
  const [apiUrl, setApiUrl] = useState(API_BASE);
  const [editingUrl, setEditingUrl] = useState(false);
  const [notificationsEnabled, setNotificationsEnabled] = useState(true);

  const handleLogout = () => {
    Alert.alert('Sign out', 'Are you sure you want to sign out?', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Sign out', style: 'destructive', onPress: () => logout() },
    ]);
  };

  const handleSaveApiUrl = () => {
    const cleaned = apiUrl.trim().replace(/\/$/, '');
    if (!cleaned.startsWith('http')) {
      Alert.alert('Invalid URL', 'API URL must start with http:// or https://');
      return;
    }
    setApiBase(cleaned);
    setApiUrl(cleaned);
    setEditingUrl(false);
    Alert.alert('Saved', 'API URL updated. Restart the app if needed.');
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.screenTitle}>Settings</Text>

        {/* Profile */}
        <Text style={styles.sectionLabel}>Profile</Text>
        <View style={styles.card}>
          <View style={styles.profileRow}>
            <View style={styles.avatarBox}>
              <Text style={styles.avatarLetter}>{(user?.name ?? 'U')[0].toUpperCase()}</Text>
            </View>
            <View style={styles.profileText}>
              <Text style={styles.profileName}>{user?.name ?? '—'}</Text>
              <Text style={styles.profileEmail}>{user?.email ?? '—'}</Text>
              <View style={styles.roleBadge}>
                <Text style={styles.roleBadgeText}>{ROLE_LABELS[user?.role ?? ''] ?? user?.role}</Text>
              </View>
            </View>
          </View>
        </View>

        {/* Preferences */}
        <Text style={styles.sectionLabel}>Preferences</Text>
        <View style={styles.card}>
          <SettingRow
            icon="notifications"
            label="Push notifications"
            right={
              <Switch
                value={notificationsEnabled}
                onValueChange={setNotificationsEnabled}
                trackColor={{ false: COLORS.border, true: COLORS.primary }}
                thumbColor="#fff"
              />
            }
          />
        </View>

        {/* Connection */}
        <Text style={styles.sectionLabel}>Connection</Text>
        <View style={styles.card}>
          {editingUrl ? (
            <View style={styles.urlEditSection}>
              <TextInput
                style={styles.urlInput}
                value={apiUrl}
                onChangeText={setApiUrl}
                autoCapitalize="none"
                autoCorrect={false}
                placeholder="https://api.yourserver.com"
                placeholderTextColor={COLORS.textMuted}
                keyboardType="url"
              />
              <View style={styles.urlBtnRow}>
                <TouchableOpacity
                  style={[styles.urlBtn, styles.urlCancelBtn]}
                  onPress={() => { setEditingUrl(false); setApiUrl(API_BASE); }}
                >
                  <Text style={styles.urlCancelText}>Cancel</Text>
                </TouchableOpacity>
                <TouchableOpacity style={[styles.urlBtn, styles.urlSaveBtn]} onPress={handleSaveApiUrl}>
                  <Text style={styles.urlSaveText}>Save</Text>
                </TouchableOpacity>
              </View>
            </View>
          ) : (
            <SettingRow
              icon="server"
              label="API Server"
              value={apiUrl}
              onPress={() => setEditingUrl(true)}
            />
          )}
        </View>

        {/* About */}
        <Text style={styles.sectionLabel}>About</Text>
        <View style={styles.card}>
          <SettingRow icon="information-circle" label="Version" value="1.0.0" />
          <View style={styles.divider} />
          <SettingRow icon="business" label="Company" value={user?.company_name ?? '—'} />
        </View>

        {/* Security */}
        <Text style={styles.sectionLabel}>Security</Text>
        <View style={styles.card}>
          <TwoFASection totpEnabled={user?.totp_enabled ?? false} />
        </View>

        {/* Sign out */}
        <Text style={styles.sectionLabel}>Account</Text>
        <View style={styles.card}>
          <SettingRow icon="log-out" label="Sign out" onPress={handleLogout} danger />
        </View>

        <Text style={styles.footer}>Secretary AI · All data is private to your company</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  content: { paddingHorizontal: 16, paddingBottom: 40 },
  screenTitle: { fontSize: 22, fontWeight: '800', color: COLORS.text, paddingVertical: 16 },
  sectionLabel: { fontSize: 12, fontWeight: '700', color: COLORS.textMuted, textTransform: 'uppercase', letterSpacing: 0.5, marginTop: 16, marginBottom: 6 },
  card: { backgroundColor: COLORS.surface, borderRadius: RADIUS.lg, overflow: 'hidden', ...SHADOW.sm },
  profileRow: { flexDirection: 'row', alignItems: 'center', padding: 16, gap: 14 },
  avatarBox: { width: 52, height: 52, borderRadius: 26, backgroundColor: COLORS.primary, justifyContent: 'center', alignItems: 'center' },
  avatarLetter: { color: '#fff', fontWeight: '800', fontSize: 22 },
  profileText: { flex: 1 },
  profileName: { fontSize: 17, fontWeight: '700', color: COLORS.text },
  profileEmail: { fontSize: 13, color: COLORS.textSecondary, marginTop: 2 },
  roleBadge: { backgroundColor: COLORS.primaryLight, borderRadius: 99, paddingHorizontal: 8, paddingVertical: 2, alignSelf: 'flex-start', marginTop: 6 },
  roleBadgeText: { fontSize: 11, fontWeight: '700', color: COLORS.primary },
  row: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 13, gap: 12 },
  rowIcon: { width: 34, height: 34, borderRadius: RADIUS.sm, justifyContent: 'center', alignItems: 'center' },
  rowContent: { flex: 1 },
  rowLabel: { fontSize: 15, color: COLORS.text },
  rowValue: { fontSize: 12, color: COLORS.textMuted, marginTop: 1 },
  divider: { height: 1, backgroundColor: COLORS.border, marginHorizontal: 16 },
  urlEditSection: { padding: 16 },
  urlInput: { borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.md, padding: 10, fontSize: 14, color: COLORS.text, marginBottom: 10 },
  urlBtnRow: { flexDirection: 'row', gap: 8 },
  urlBtn: { flex: 1, borderRadius: RADIUS.md, paddingVertical: 9, alignItems: 'center' },
  urlCancelBtn: { borderWidth: 1, borderColor: COLORS.border },
  urlCancelText: { color: COLORS.textSecondary, fontWeight: '600' },
  urlSaveBtn: { backgroundColor: COLORS.primary },
  urlSaveText: { color: '#fff', fontWeight: '700' },
  footer: { marginTop: 24, textAlign: 'center', fontSize: 12, color: COLORS.textMuted },
});

const twoFAStyles = StyleSheet.create({
  container: { padding: 16 },
  title: { fontSize: 16, fontWeight: '700', color: COLORS.text, marginBottom: 8 },
  body: { fontSize: 13, color: COLORS.textSecondary, marginBottom: 16 },
  statusRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 14 },
  statusText: { fontSize: 14, fontWeight: '600' },
  secretLabel: { fontSize: 12, fontWeight: '600', color: COLORS.textMuted, marginBottom: 4 },
  secret: {
    fontFamily: 'monospace',
    fontSize: 13,
    color: COLORS.text,
    backgroundColor: COLORS.background,
    borderRadius: RADIUS.sm,
    padding: 10,
    marginBottom: 16,
    letterSpacing: 1,
  },
  label: { fontSize: 13, fontWeight: '600', color: COLORS.text, marginBottom: 6 },
  input: {
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: RADIUS.md,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
    color: COLORS.text,
    backgroundColor: COLORS.background,
    marginBottom: 16,
  },
  codeInput: { fontSize: 22, letterSpacing: 8, textAlign: 'center' },
  qrBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    borderWidth: 1,
    borderColor: COLORS.primary,
    borderRadius: RADIUS.md,
    paddingVertical: 10,
    paddingHorizontal: 14,
    marginBottom: 16,
    alignSelf: 'flex-start',
  },
  qrBtnText: { color: COLORS.primary, fontWeight: '600', fontSize: 14 },
  row: { flexDirection: 'row', gap: 10 },
  btn: { flex: 1, borderRadius: RADIUS.md, paddingVertical: 11, alignItems: 'center' },
  cancelBtn: { borderWidth: 1, borderColor: COLORS.border },
  cancelText: { color: COLORS.textSecondary, fontWeight: '600' },
  primaryBtn: { backgroundColor: COLORS.primary },
  primaryText: { color: '#fff', fontWeight: '700' },
  dangerBtn: { backgroundColor: COLORS.danger },
  dangerOutlineBtn: { borderWidth: 1, borderColor: COLORS.danger },
  dangerOutlineText: { color: COLORS.danger, fontWeight: '600' },
  disabledBtn: { opacity: 0.6 },
});

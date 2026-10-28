import 'react-native-gesture-handler';
import React, { useEffect } from 'react';
import { Platform, Text, View } from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import * as Notifications from 'expo-notifications';
import { setApiBase } from './src/api/client';
import { registerPushToken } from './src/api/notifications';
import { useAuthStore } from './src/stores/authStore';
import RootNavigator from './src/navigation/RootNavigator';

// Configure API base URL — set EXPO_PUBLIC_API_URL in .env
const API_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';
let apiConfigError = false;
try {
  setApiBase(API_URL);
} catch {
  // An unsafe/missing production API configuration must not receive login,
  // refresh or chat credentials. Show an actionable error without a session.
  apiConfigError = true;
}

// Show notifications while app is in foreground
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: true,
  }),
});

async function registerForPushNotifications() {
  if (Platform.OS === 'web') return;
  const { status: existing } = await Notifications.getPermissionsAsync();
  let finalStatus = existing;
  if (existing !== 'granted') {
    const { status } = await Notifications.requestPermissionsAsync();
    finalStatus = status;
  }
  if (finalStatus !== 'granted') return;

  try {
    const tokenData = await Notifications.getExpoPushTokenAsync();
    const platform = Platform.OS as 'ios' | 'android';
    await registerPushToken(tokenData.data, platform);
  } catch {
    // Non-fatal — app works fine without push tokens
  }
}

function PushNotificationSetup() {
  const { isAuthenticated } = useAuthStore();

  useEffect(() => {
    if (isAuthenticated) {
      registerForPushNotifications();
    }
  }, [isAuthenticated]);

  return null;
}

export default function App() {
  if (apiConfigError) {
    return (
      <SafeAreaProvider>
        <View style={{ flex: 1, justifyContent: 'center', padding: 24 }} accessible accessibilityRole="alert">
          <Text style={{ fontSize: 20, fontWeight: '700' }}>SecretaryAI server not configured</Text>
          <Text style={{ marginTop: 12 }}>A secure API connection is required. Contact your administrator or install a correctly configured build.</Text>
        </View>
      </SafeAreaProvider>
    );
  }
  return (
    <SafeAreaProvider>
      <NavigationContainer>
        <StatusBar style="dark" />
        <PushNotificationSetup />
        <RootNavigator />
      </NavigationContainer>
    </SafeAreaProvider>
  );
}

import 'react-native-gesture-handler';
import React, { useEffect } from 'react';
import { Platform } from 'react-native';
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
setApiBase(API_URL);

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

import 'react-native-gesture-handler';
import React from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import { setApiBase } from './src/api/client';
import RootNavigator from './src/navigation/RootNavigator';

// Configure API base URL — update this to your server URL
const API_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';
setApiBase(API_URL);

export default function App() {
  return (
    <SafeAreaProvider>
      <NavigationContainer>
        <StatusBar style="dark" />
        <RootNavigator />
      </NavigationContainer>
    </SafeAreaProvider>
  );
}

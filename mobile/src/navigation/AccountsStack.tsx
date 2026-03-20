import React from 'react';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import AccountsScreen from '../screens/AccountsScreen';
import AccountDetailScreen from '../screens/AccountDetailScreen';
import SettingsScreen from '../screens/SettingsScreen';
import { AccountsStackParamList } from './types';
import { COLORS } from '../theme';
import { TouchableOpacity } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

const Stack = createNativeStackNavigator<AccountsStackParamList & { Settings: undefined }>();

export default function AccountsStack() {
  return (
    <Stack.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: COLORS.surface },
        headerTintColor: COLORS.text,
        headerShadowVisible: false,
        headerBackTitle: 'Back',
      }}
    >
      <Stack.Screen
        name="AccountsList"
        component={AccountsScreen}
        options={({ navigation }) => ({
          headerShown: true,
          title: 'Accounts',
          headerRight: () => (
            <TouchableOpacity onPress={() => (navigation as any).navigate('Settings')} style={{ marginRight: 4 }}>
              <Ionicons name="settings-outline" size={22} color={COLORS.textSecondary} />
            </TouchableOpacity>
          ),
        })}
      />
      <Stack.Screen
        name="AccountDetail"
        component={AccountDetailScreen}
        options={({ route }) => ({
          title: (route.params as any).accountName ?? 'Account',
          headerShown: true,
        })}
      />
      <Stack.Screen
        name={'Settings' as any}
        component={SettingsScreen}
        options={{ title: 'Settings', headerShown: true }}
      />
    </Stack.Navigator>
  );
}

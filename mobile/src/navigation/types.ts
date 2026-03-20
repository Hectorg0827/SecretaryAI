import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import type { BottomTabScreenProps } from '@react-navigation/bottom-tabs';

// Root stack (auth gate)
export type RootStackParamList = {
  Login: undefined;
  App: undefined;
};

// Bottom tab navigator
export type AppTabParamList = {
  Dashboard: undefined;
  Chat: { conversationId?: string } | undefined;
  Accounts: undefined;
  Inventory: undefined;
  Approvals: undefined;
};

export type LoginScreenProps = NativeStackScreenProps<RootStackParamList, 'Login'>;
export type AppScreenProps = NativeStackScreenProps<RootStackParamList, 'App'>;
export type DashboardScreenProps = BottomTabScreenProps<AppTabParamList, 'Dashboard'>;
export type ChatScreenProps = BottomTabScreenProps<AppTabParamList, 'Chat'>;
export type AccountsScreenProps = BottomTabScreenProps<AppTabParamList, 'Accounts'>;
export type InventoryScreenProps = BottomTabScreenProps<AppTabParamList, 'Inventory'>;
export type ApprovalsScreenProps = BottomTabScreenProps<AppTabParamList, 'Approvals'>;

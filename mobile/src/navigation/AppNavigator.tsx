import React from 'react';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { Ionicons } from '@expo/vector-icons';
import { AppTabParamList } from './types';
import DashboardScreen from '../screens/DashboardScreen';
import ChatScreen from '../screens/ChatScreen';
import ReportsScreen from '../screens/ReportsScreen';
import AccountsStack from './AccountsStack';
import InventoryScreen from '../screens/InventoryScreen';
import ApprovalsScreen from '../screens/ApprovalsScreen';
import LogisticsScreen from '../screens/LogisticsScreen';
import ComplianceScreen from '../screens/ComplianceScreen';
import { COLORS } from '../theme';
import { useAuthStore } from '../stores/authStore';

const Tab = createBottomTabNavigator<AppTabParamList>();

type IoniconName = React.ComponentProps<typeof Ionicons>['name'];

const TAB_ICONS: Record<keyof AppTabParamList, [IoniconName, IoniconName]> = {
  Dashboard:  ['home', 'home-outline'],
  Chat:       ['chatbubbles', 'chatbubbles-outline'],
  Reports:    ['bar-chart', 'bar-chart-outline'],
  Accounts:   ['people', 'people-outline'],
  Inventory:  ['cube', 'cube-outline'],
  Logistics:  ['git-network', 'git-network-outline'],
  Compliance: ['shield-checkmark', 'shield-checkmark-outline'],
  Approvals:  ['checkmark-circle', 'checkmark-circle-outline'],
};

export default function AppNavigator() {
  // UI discoverability follows the server /auth/me role. Each API still enforces
  // permissions independently; hiding a tab is not a substitute for backend RBAC.
  const role = useAuthStore((state) => state.user?.role ?? 'viewer');
  const isLeader = role === 'owner' || role === 'manager';
  const canChat = isLeader || role === 'sales_rep';
  // Sales-rep account ownership is currently unknown in the backend; keep
  // portfolio screens closed until the record-scope contract is verified.
  const canAccessAccounts = isLeader || role === 'back_office';
  const canAccessInventory = canChat || role === 'back_office';

  return (
    <Tab.Navigator
      screenOptions={({ route }) => {
        const [active, inactive] = TAB_ICONS[route.name] ?? ['ellipse', 'ellipse-outline'];
        return {
          tabBarIcon: ({ focused, color, size }) => (
            <Ionicons name={focused ? active : inactive} size={size} color={color} />
          ),
          tabBarActiveTintColor: COLORS.primary,
          tabBarInactiveTintColor: COLORS.textMuted,
          tabBarStyle: { borderTopColor: COLORS.border, backgroundColor: COLORS.surface },
          headerShown: false,
        };
      }}
    >
      <Tab.Screen name="Dashboard" component={DashboardScreen} />
      {canChat && <Tab.Screen name="Chat" component={ChatScreen} />}
      {isLeader && <Tab.Screen name="Reports" component={ReportsScreen} />}
      {canAccessAccounts && <Tab.Screen name="Accounts" component={AccountsStack} options={{ headerShown: false }} />}
      {canAccessInventory && <Tab.Screen name="Inventory" component={InventoryScreen} />}
      {isLeader && <Tab.Screen name="Logistics" component={LogisticsScreen} />}
      {isLeader && <Tab.Screen name="Compliance" component={ComplianceScreen} />}
      {isLeader && <Tab.Screen name="Approvals" component={ApprovalsScreen} />}
    </Tab.Navigator>
  );
}

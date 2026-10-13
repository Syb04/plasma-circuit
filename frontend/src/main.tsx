import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import AuthGate from './Auth';
import { initializeTheme } from './theme';
import '@xyflow/react/dist/style.css';
import './theme.css';
import './styles.css';

initializeTheme();
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><AuthGate>{(user,onLogout,onPassword)=><App key={`${user.id}:${user.is_admin}`} user={user} onLogout={onLogout} onPassword={onPassword}/>}</AuthGate></React.StrictMode>);

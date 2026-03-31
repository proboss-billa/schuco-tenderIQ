"use client";
import { C } from "@/lib/design";

export const SchucoFull = ({ h = 22 }) => (
  <svg height={h} viewBox="0 0 200 55" fill="none" xmlns="http://www.w3.org/2000/svg">
    <g>
      <path d="M22.5 35.5C18.2 35.5 14.8 34.2 12.5 32.2L15.8 27.8C17.6 29.3 19.8 30.3 22.3 30.3C24.8 30.3 26.2 29.2 26.2 27.5C26.2 25.8 25 25 21.5 24C16.5 22.5 13.5 20.8 13.5 16.2C13.5 11.8 17 8.5 22.5 8.5C26.5 8.5 29.5 9.8 31.8 11.8L28.8 16.2C27 14.8 25 14 22.5 14C20.2 14 19 15 19 16.5C19 18.2 20.5 18.8 24 19.8C29 21.3 31.8 23.2 31.8 27.5C31.8 32.2 28 35.5 22.5 35.5Z" fill={C.text1}/>
      <path d="M50.5 35.5C42.5 35.5 36.5 29.5 36.5 22C36.5 14.5 42.5 8.5 50.5 8.5C55.5 8.5 59 10.5 61.5 13.5L57.2 17C55.5 15 53.2 13.8 50.5 13.8C45.8 13.8 42.2 17.5 42.2 22C42.2 26.5 45.8 30.2 50.5 30.2C53.2 30.2 55.5 29 57.2 27L61.5 30.5C59 33.5 55.5 35.5 50.5 35.5Z" fill={C.text1}/>
      <path d="M68 35V9H73.5V19H84.5V9H90V35H84.5V24.2H73.5V35H68Z" fill={C.text1}/>
      <path d="M98 29.5V9H103.5V29.5C103.5 32 105 33 107 33C109 33 110.5 32 110.5 29.5V9H116V29.5C116 34.5 112.5 37.5 107 37.5C101.5 37.5 98 34.5 98 29.5Z" fill={C.text1}/>
      <circle cx="103.5" cy="5" r="2" fill={C.text1}/>
      <circle cx="110.5" cy="5" r="2" fill={C.text1}/>
      <path d="M107 1L109 4.5H105L107 1Z" fill={C.text1} opacity="0.6"/>
      <path d="M134.5 35.5C126.5 35.5 120.5 29.5 120.5 22C120.5 14.5 126.5 8.5 134.5 8.5C139.5 8.5 143 10.5 145.5 13.5L141.2 17C139.5 15 137.2 13.8 134.5 13.8C129.8 13.8 126.2 17.5 126.2 22C126.2 26.5 129.8 30.2 134.5 30.2C137.2 30.2 139.5 29 141.2 27L145.5 30.5C143 33.5 139.5 35.5 134.5 35.5Z" fill={C.text1}/>
      <path d="M163 35.5C155 35.5 149 29.5 149 22C149 14.5 155 8.5 163 8.5C171 8.5 177 14.5 177 22C177 29.5 171 35.5 163 35.5ZM163 30.2C167.5 30.2 171 26.5 171 22C171 17.5 167.5 13.8 163 13.8C158.5 13.8 155 17.5 155 22C155 26.5 158.5 30.2 163 30.2Z" fill={C.text1}/>
    </g>
    <rect x="13" y="42" width="164" height="7" rx="1.5" fill={C.green}/>
  </svg>
);

export const SchucoMark = () => (
  <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
    <rect width="32" height="32" rx="7" fill={C.bg2}/>
    <text x="4" y="18" fontFamily="Arial Black,Arial" fontWeight="900" fontSize="9" fill={C.text1} letterSpacing="0.3">SCHÜCO</text>
    <rect x="4" y="21" width="24" height="4" rx="1" fill={C.green}/>
  </svg>
);

export const SendIcon = () => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>;
export const UploadIcon = () => <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12"/></svg>;
export const FileIcon = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>;
export const ChatIcon = () => <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>;
export const SettingsIcon = () => <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>;
export const DownloadIcon = () => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>;
export const CloseIcon = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>;
export const PlusIcon = () => <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>;
export const MenuIcon = () => <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/></svg>;
export const BackIcon = () => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6"/></svg>;
export const EyeIcon = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>;
export const EyeOffIcon = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19m-6.72-1.07a3 3 0 11-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>;
export const CheckIcon = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>;
export const PanelIcon = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="15" y1="3" x2="15" y2="21"/></svg>;
export const LogoutIcon = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9"/></svg>;
export const TrashIcon = () => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>;
export const EditIcon = () => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>;
export const MoreIcon = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></svg>;
export const MailIcon = () => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M22 7l-10 7L2 7"/></svg>;

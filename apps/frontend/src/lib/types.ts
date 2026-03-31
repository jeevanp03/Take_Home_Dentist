/** Shared types for the dental chatbot frontend. */

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
  toolStatus?: string;
  error?: boolean;
}

export type PatientMode = "returning" | "new" | "question";

export interface PatientContext {
  patientId: string | null;
  patientName: string | null;
  mode: PatientMode;
  upcomingAppointments: AppointmentDisplay[];
  needsInfo: string[];
}

export interface AppointmentDisplay {
  id: string;
  type: string;
  date: string;
  time: string;
  provider: string;
  status?: string;
}

export type AppScreen = "welcome" | "chat";

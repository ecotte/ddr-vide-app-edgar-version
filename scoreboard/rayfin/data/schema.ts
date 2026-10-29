import { LiveBoard } from './LiveBoard.js';
import { GameSession } from './GameSession.js';
import { UserSession } from './UserSession.js';

export type AppSchema = {
  LiveBoard: LiveBoard;
  GameSession: GameSession;
  UserSession: UserSession;
};

export const schema = [LiveBoard, GameSession, UserSession];

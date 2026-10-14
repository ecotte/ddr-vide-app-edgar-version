import { LiveBoard } from './LiveBoard.js';

export type AppSchema = {
  LiveBoard: LiveBoard;
};

export const schema = [LiveBoard];
